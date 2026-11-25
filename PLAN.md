# Plan: Pre-submission claim checker

**Status:** draft for review (v3: deterministic fixes are applied automatically; people handle only ambiguous cases)

## 1. The problem in one line
Billing teams at home-care agencies lose time and cash when payers deny claims. We check every claim **before it's
submitted**, predict whether it will be paid, and if not, say **why** and **how to fix it**.

**Who it's for:** agency billing teams. **Good looks like:** high accuracy on past claims, with a clear reason and fix
for every flagged claim, and people approving anything high-stakes.

## 2. The big idea
> **Rules check the claims and apply the obvious fixes. The AI helps maintain the rules and explain them. People only
> handle what's ambiguous.**

- **If a problem has one clear, rule-based fix, the system applies it automatically.** People are only pulled in when
  the answer isn't clear-cut (no fix exists, more than one fix is possible, or it needs someone outside the system).

- **Checking a claim is deterministic.** It's the same answer every time, it can be audited, it's free to run, and it's
  fast. No LLM decides whether a claim gets paid.
- **The system never guesses.** If it can't read a value (an unknown payer spelling, an odd date format), it doesn't
  guess. It holds the claim and asks a person.
- **The LLM does the jobs rules are bad at:**
  1. Reading payer remarks on denials we *didn't* predict, grouping them, and proposing a new rule.
  2. Turning a claim's problems and fixes into a plain-English message a billing person can act on.
- **People stay in the loop** through a Discord bot (standing in for Slack). They see every auto fix (and can undo it),
  handle the ambiguous cases, and approve new rules with ✅ / ❌ or a text reply.

## 3. How it fits together

```
 data files (read-only)
        │
        ▼
 ┌──────────────┐ can't read ┌────────────────────┐
 │ 1. Load and  │───────────▶│ Discord            │
 │    clean     │            │ #data-issues       │──┐ approved mapping
 │  (runtime)   │◀───────────│ ✅ / ❌ / reply    │  │ added to
 └──────┬───────┘   re-run   └────────────────────┘  │ mappings.yaml
        │ clean claims        ▲                       │
        ▼                     └───────────────────────┘
 ┌───────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
 │ 2. Rule engine        │───▶│ 3. Fix suggester   │───▶│ Discord            │
 │    + ledger (stateful)│    │    (rules decide,  │    │ #claim-review      │
 │    rules from YAML    │    │     LLM words it)  │    │ ✅ / ❌ / reply    │
 └──────────┬────────────┘    └────────────────────┘    └────────────────────┘
            ▼
 ┌───────────────────────┐
 │ 4. Back-test vs       │
 │    claims history     │
 └──────────┬────────────┘
            │ misses
            ▼
 ┌───────────────────────┐    ┌────────────────────┐
 │ 5. Unexplained-denial │───▶│ Discord            │
 │    grouping (LLM)     │    │ #rule-proposals    │
 │  no pattern? → person │    │ #needs-investig.   │
 └───────────────────────┘    │ ✅ / ❌ / reply    │
            ▲                 └─────────┬──────────┘
            └───── approved rule ───────┘
                   added to rules YAML
```

## 4. Components

### 1. Load and clean (runtime, never edits the source files)
**Known problems are fixed automatically, and every fix is logged:**
- Map payer names to IDs using a lookup file, `mappings.yaml` (`NORTHWIND HP` → `PY01`, etc.).
- Parse the two date formats we've seen (`2026-09-13` and US `09/13/2026`).
- Strip whitespace and upper-case service codes and modifiers.
- Drop exact duplicate rows in history (32), so they don't double-count units.

**Anything not recognised is held for a person, not guessed.** A claim is put on hold if:
- the payer name, service code or modifier isn't in `mappings.yaml` (e.g. `Northwnd Health`, `T1019-A`)
- a date doesn't match a known format (e.g. `13-09-2026`, `Sept 13`), or isn't a real date (`02/30/2026`)
- a required field is missing, or units aren't a positive whole number
- it refers to something we don't know (an `auth_id`, patient or original claim not in the data)

A claim on hold isn't checked or predicted. Its status is **"needs data fix"**, and it's posted to #data-issues. For
unknown names, the bot suggests the closest known value (simple string matching, no LLM). If a person approves the
suggestion, it's added to `mappings.yaml` and the claim is re-run. So the cleaner learns new spellings from people.

### 2. Rule engine and ledger
- Merge history and to-check claims into **one timeline sorted by submission date** (same-day ties by `claim_id`;
  checked against history).
- Walk the timeline in order. For each claim, **run every rule and record every problem found**. Then pick the
  **one** code the payer will report, using that payer's precedence order.
- The **ledger** holds the running state:
  - units left on each authorization (D02)
  - each member's paid units per day for Harbor's 32-unit daily cap (D06)
  - every claim seen so far (D05 duplicates)
- Only claims predicted as **paid** draw down units.
- **Rules live in a YAML file per payer** (limits, rounding, modifiers, precedence, contract rules), not hard-coded.
  That's what lets an approved rule proposal go live without changing code.

| Code | Check |
|---|---|
| D01 | No verified EVV visit for patient, service and date (clock-in date) |
| D09 | Units > verified minutes, using the payer's rounding (Northwind: 8-min rule; others: whole units only) |
| D03 | Filed after the payer's limit (Northwind 30, or 45 for DOS from 1 Aug; Summit 60; Harbor none) |
| D04 | Harbor personal care (T1019) needs U2, or U3 at weekends from 1 Sept |
| D05 | New claim matching an earlier one (patient, service, date, payer) |
| D07 / D08 | Authorization missing / doesn't match patient, service, payer or date |
| D02 | Units > units left on the authorization (ledger) |
| D06 | Harbor: member's paid units that day would go over 32 (ledger) |
| D10 | Summit amendment 3: DOS from 1 Jul must be whole hours (units a multiple of 4) |

**Precedence (first match is the code the payer reports):**
| Payer | Order |
|---|---|
| Northwind (PY01) | D01 > D09 > D07 > D08 > D02 > D03 > D05 |
| Harbor (PY02) | D01 > D09 > D07 > D08 > D04 > D05 > D06 > D02 |
| Summit (PY03) | D01 > D07 > D08 > D10 > D09 > D02 > D03 > D05 |

**Extra checks:**
- **Corrected claims (frequency 7):** look up the original claim and check (a) its original problem is now fixed and
  (b) the correction hasn't hit a new or previously hidden problem. In the data, 2 of 14 corrections still fail
  (C021766 fixed its missing auth but still has unit problems that Summit's ordering hid; C021797 is now over its auth).
- **Hidden outcomes (back-test only):** if the history only adds up when a to-check claim was denied, flag that claim
  to #needs-investigation even though all its rules pass (e.g. C021301).

### 3. Fix suggester
**It lists every problem on the claim, not just the code the payer will report.** 29 to-check claims have 2+ problems.
Fixing only the reported one gets the claim denied again for the next.

**The fix is decided by rules.** Some codes need different fixes depending on the situation:

| Code | Situation | Fix | Who |
|---|---|---|---|
| D01 | No visit recorded | Don't bill. Possible compliance issue | Person |
| | Visit exists but `rejected` / `manual_edit_pending` | Chase the visit approval, then submit | Coordinator |
| | Verified visit on the day before or after | Check whether the date of service is wrong | Person |
| D02 | Authorization used up | Request a new authorization from the payer | Person |
| D03 | Past the filing limit | Can't fix | Person (write off) |
| D04 | Wrong modifier | Change to U2 / U3 | Auto |
| D05 | Original was paid | Don't send (double billing) | Auto |
| | Original was denied | Resubmit as a correction (frequency 7 + original claim ID) | Auto |
| D06 | Over Harbor's 32/day | Suggest units = what's left that day (or which service to reduce) | Person |
| D07 / D08 | Missing or wrong authorization | Suggest the valid auth, e.g. "use AUTH11348" (one always exists in this data). **Never auto-applied**: it attaches the claim to a payer approval | Person |
| D09 | Units over visit time | Suggest units = verified amount, e.g. "14 units (3h 32m)", or fix the visit record | Person |
| D10 | Summit not whole hours | Suggest rounding down to a multiple of 4 | Person |

**Auto fixes are applied without asking**, but only if all of these hold:
1. The fix is deterministic and **unique**, and on the approved list below.
   **Approved auto fixes (user decision, 2026-11-25):** D04 modifier, D05 drop a duplicate of a paid claim, D05 convert a
   duplicate of a denied claim into a correction. **Unit cuts (D09/D06/D10) are never automatic** (revised 2026-11-25):
   cutting gives up revenue and the visit record may be wrong, so a person decides. The system suggests the exact
   number, worked out by applying every unit fix and re-checking (e.g. C019037: 20 → 16 units). **Authorization fixes (D07/D08) are never automatic.**
2. It **never increases** the billed amount. Auto fixes only correct codes or stop a claim; they never change units.
3. After **every** problem on the claim is fixed, the claim is **re-run and passes**.

If any condition fails, or any problem on the claim is a "Person" fix, the whole claim goes to a person.

- Auto-fixed claims are written to a new `corrected_claims.csv` (fixed claim, plus what changed and why). The source
  files are never edited. In production this would write back to the agency's billing system.
- Every auto fix is logged and posted to #claim-review as an FYI, so it can be audited and undone before submission.
- **The LLM (Haiku 5.5) only writes the explanation.** It gets the claim facts, every problem and every fix, and
  writes 2–3 plain sentences. It can't change the decision. If the LLM fails, fall back to a template message.

### 4. Back-test (how we know it works)
- Replay the claims history through the engine and compare against actual outcomes.
- Report accuracy, plus per-code precision and recall, as a confusion matrix.
- **Time holdout:** set the rules on June–August, test on September onwards, so we're not marking our own homework.
- Early prototype (rules set on the full history): **98.1%** exact match, **99.7%** excluding the generic D99s.
- Every claim we got wrong goes to step 5.

### 5. Unexplained-denial grouping (LLM)
- Input: denied claims the engine predicted as paid, or gave a different code, with the payer's remarks and claim fields.
- The LLM (Sonnet 5.5) groups them by likely cause and, for each group, proposes **a rule in the YAML format**, with
  the evidence (how many claims, example IDs, date range).
- We test the proposed rule on history before posting it: how many claims it would catch, and how many paid claims it
  would wrongly deny.
- Denials the LLM **can't explain with a rule** (no clear pattern, or generic remarks like "contact payer") are **not
  dropped**. They go to a person with everything we know: claim fields, payer remark, the rules that passed, and the
  LLM's best guess. People often have context we don't, or can call the payer.
- Expected result on this data: it should rediscover "Summit amendment 3", and send the generic D99s to a person.

## 5. Discord bot (stand-in for Slack)

**#data-issues**: one message per claim the cleaner couldn't read:
```
🧩 C021900 · payer "Northwnd Health" not recognised
Closest match: Northwind Health Plan (PY01)
React ✅ to map it to PY01, ❌ to reject, or reply with the right value.
```
- ✅ the mapping is added to `mappings.yaml`, and every claim on hold with that value is re-run.
- ❌ the claim stays on hold and goes back to the agency to fix at the source.
- A text reply gives the right value (e.g. "PY03", or "date is 9 March"). The bot applies it and re-runs the claim.

**#claim-review** has two kinds of message.

*Auto-fixed (FYI, no action needed):*
```
🔧 C020691 · Harbor · T1019 · DOS 14 Sep: AUTO-FIXED
Would have been: DENIED, D04. Problem found and fixed:
  1. D04: modifier U1 should be U2
Changed: modifier U1 → U2. Re-checked: passes ✓
React ❌ to undo before submission.
```
- No reaction is needed. ❌ undoes the fix, sends the claim to a person, and is logged as feedback. Repeated undos on
  one rule suggest the rule or its fix is wrong.

*Needs a person:*
```
⚠️ C018952 · Northwind · T1019 · DOS 22 Oct: NEEDS A PERSON
Predicted: DENIED, D03 (filed 68 days after the visit; limit is 45)
No automatic fix: the filing deadline has passed.
React ✅ once handled, ❌ if you disagree with the flag, or reply with a note.
```
- The person makes any change in the billing system. When the corrected claim comes back in, it's re-checked and the
  bot replies with ✓ fixed, or the remaining problems.
- ❌ is logged as feedback. A text reply is saved as a note (e.g. "payer approved an exception by phone").

**#rule-proposals**: one message per proposed rule:
```
💡 Proposed rule: Summit units must be whole hours (DOS ≥ 1 Jul 2026)
Evidence: 50 denials, all Summit, remark "amendment 3", 0 paid claims affected
React ✅ to add to rules, ❌ to reject, or reply to ask for changes.
```
- ✅ the rule is written to the YAML and the back-test is re-run.
- ❌ the proposal is rejected and remembered, so it isn't proposed again.
- A text reply is sent back to the LLM to revise the proposal, then the bot reposts it.

**#needs-investigation**: one message per denial or outcome nobody can explain:
```
❓ C014376 · Summit · DOS 22 Jun · DENIED D99 "Under review - see payer correspondence"
All rule checks passed. LLM: no pattern found across 61 similar denials.
React ✅ once resolved, ❌ if it can't be resolved, or reply with what you found.
```
- A text reply ("payer says caregiver not credentialed") is fed back into grouping, so a person's finding can become a
  new rule proposal.

All decisions are written to a log file (who, what, when).

## 6. Outputs
- `predictions.csv` (`claim_id,status,denial_code`) for the 546 claims to check, **as submitted** (before fixes).
  Claims on hold get status `needs_data_fix`.
- `corrected_claims.csv`: auto-fixed claims with what changed, plus a summary (how many claims were auto-fixed, how many
  need a person, and how many dollars were recovered vs written off).
- A back-test report (accuracy and confusion matrix).
- Discord messages for data issues, flagged claims, rule proposals and investigations.
- A decision log.

## 7. Tech
- Python with pandas. `discord.py` for the bot. Anthropic SDK with Haiku 5.5 for explanations and Sonnet 5.5 for
  grouping.
- API key and Discord token come from environment variables and are never committed.
- Runs as `python run.py check` (engine, back-test and predictions) and `python run.py bot` (Discord).

```
src/
  load.py        # runtime cleaning + hold anything unrecognised
  rules.py       # rule checks
  engine.py      # timeline + ledger + precedence
  fixes.py       # all problems → fixes, LLM explanation
  backtest.py    # accuracy report
  propose.py     # unexplained-denial grouping (LLM)
  bot.py         # Discord
rules/           # PY01.yaml, PY02.yaml, PY03.yaml
mappings.yaml    # known spellings of payers, service codes, modifiers
tests/           # one test per rule, plus cleaning tests with odd inputs
```

## 8. Build order
1. Load and clean (including holding unrecognised data), the rule engine and ledger, and the back-test. Iterate until
   accuracy is good. **The core works without any AI.**
2. Write `predictions.csv`.
3. Fix suggester: all problems, deterministic fixes, auto-apply with guardrails and re-check, `corrected_claims.csv`,
   then LLM wording. Corrected-claim check.
4. Unexplained-denial grouping, first by **leaving Summit amendment 3 out of the rules** to show the LLM rediscovering it.
5. Discord bot (#claim-review first, then #data-issues, #rule-proposals, #needs-investigation).
6. README and design diagram.

If time runs short, cut from the bottom. Steps 1–3 alone are a complete demo. The current data has no unreadable
values once the known fixes are applied, so the hold path will be demoed by feeding in a few odd claims (a misspelled
payer, a bad date).

## 9. Assumptions
- Both claim files are one timeline, and to-check claims draw units like real claims (the history confirms this).
- Denied claims don't draw down units. Corrected claims (code 7) are never duplicates.
- Precedence is per payer and learned from history (to confirm on the time holdout, and with more data in real life).
- `MM/DD/YYYY` is US order. That's safe here (the second number often goes above 12, the first never does), but a
  new source with ambiguous dates would be held, not guessed.
- The generic D99 denials can't be predicted before submission (no data we hold explains them). Once they happen,
  they're **flagged to a person** in #needs-investigation, not ignored. They cap accuracy at about 1.6% below perfect.

## 10. Out of scope
- Actually submitting claims or talking to payers.
- Reserving units for claims still pending at the payer (we'd need this in production).
- Real patient data, logins, multi-agency permissions.
