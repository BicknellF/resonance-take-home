# Project instructions

## Prompt log
After responding to every user prompt, append an entry to `PROMPT_LOG.md` in this format:

```
## <n>. <YYYY-MM-DD>

**Prompt:**
> <the user's prompt, verbatim>

**Claude response:**
- <3–6 concise bullets summarising what Claude did/answered, including files created or changed and key findings>
```

Number entries sequentially and separate them with `---`.

## Data handling rule
- **Never modify the files in `Resonance - graduate preparation data/`.** All cleaning/normalisation happens at runtime when loading the data.

## Code (implemented 2026-11-25)
- Use the project virtualenv: `.venv/Scripts/python` (the Store Python's site-packages broke installing `anthropic`).
- `run.py check | propose | explain <id> | bot`. Tests: `.venv/Scripts/python -m pytest -q`.
- Layout: `src/load.py` (cleaning + holds), `rules.py` (one check per code), `ledger.py` (running state),
  `engine.py` (timeline), `fixes.py` (approved auto fixes + guardrails), `backtest.py`, `explain.py` (Haiku),
  `propose.py` (Sonnet), `bot.py` (Discord), `mappings.py`. Payer rules: `rules/PY0x.yaml`; spellings: `mappings.yaml`.
- No API key or Discord token on this machine: LLM and bot paths are untested live (they fall back to templates).

---

# Project context

Take-home for Resonance (case study: Exchange4Health, US home-care back office). Brief: frame a claims problem,
design a system (AI vs rules, human in the loop), build a working prototype on the synthetic data, and show it works
(e.g. back-test on claims history). 2–4 hours. Deliverables: repo, README (what/how/assumptions), design sketch,
optional CLAUDE.md/session history. 10-min live demo. Optional `predictions.csv` with `claim_id,status,denial_code`
for the 546 claims in `claims_to_check.csv`.

Budget note: $40 API credit. Sonnet 5.5 for most work, Haiku 5.5 if calling an LLM per claim.

## Direction set by the user (before design)
- Build an "agent" that checks each claim with **deterministic rules**.
- It must be **stateful**: keep a running ledger of authorization units used (and anything else that accumulates).
- Data cleaning happens **at runtime, not in place**.

# Findings from reading the data pack (2026-11-24)

All findings below were checked against `claims_history.csv` with exploratory scripts. "Perfect" means the rule
flagged no claim that was actually paid, and caught every claim with that code (except where precedence hides it).

## Files
| File | Rows | Notes |
|---|---|---|
| `claims_history.csv` | 3,862 (3,830 after dedup) | Has `status, paid_amount, denial_code, remark`. 2,758 paid / 1,104 denied (raw). |
| `claims_to_check.csv` | 546 | Same columns minus outcome. 532 new (`frequency_code=1`), 14 corrected (`7`). |
| `authorizations.csv` | 780 | `auth_id, patient_id, payer_id, service_code, start_date, end_date, units_authorized`. Clean. Mostly quarterly (Jun–Aug, Sep–Nov). |
| `evv_visits.csv` | 5,076 | `verified` 4,989 / `rejected` 50 / `manual_edit_pending` 37. Clean ISO datetimes. 60–554 min. 191 cross midnight. |

## Data quality issues (fix at runtime)
- **Payer names vary**: `PY01` = `NORTHWIND HP` = `Northwind Health Plan`; `PY02` = `Harbor Medicaid Plan` = `Harbor Medicaid`;
  `PY03` = `SUMMIT CARE` = `Summit Care Partners`. Auth and EVV files use clean IDs.
- **Service codes** sometimes have trailing whitespace (`"T1019 "`).
- **Modifiers**: leading/trailing spaces and lower case (`" U1"`, `"u2"`, `"U3 "`). Normalise with strip + upper.
- **Dates mixed**: `YYYY-MM-DD` and `MM/DD/YYYY` (confirmed US order: first part never > 12, second part often > 12).
  Affects `date_of_service` and `submitted_date` in both claims files.
- **32 exact duplicate rows** in `claims_history.csv` (same `claim_id`, all fields identical), which are export artefacts.
  Must drop them, otherwise they double-count auth drawdown and create false D05s.
- 84 history / 9 to-check claims have empty `auth_id`.
- No negative submission lags. The submission dates run up to Feb 2027 (no "today", so judge each claim on its own dates).

## Rules confirmed against history
| Code | Rule | Payers | Evidence |
|---|---|---|---|
| D01 | No **verified** EVV visit for patient + service + date of service. Visit date = **clock-in date**. `rejected`/`manual_edit_pending` don't count. | All | Perfect (181/181, 0 paid flagged). 78 D01s had only unverified visits. |
| D09 | Billed units > units supported by the **sum** of verified minutes that day (same patient + service). PY01: 8-minute rule (`floor(m/15) + (m%15 >= 8)`). PY02 and PY03: `floor(m/15)` (whole units only). | All | PY01 8-min rule perfect. PY01 floor would wrongly deny 581 paid claims. PY02 floor perfect. PY03 floor perfect after precedence. |
| D03 | Timely filing: `submitted - DOS > limit`. PY01: 30 days for DOS < 2026-08-01, **45 days** for DOS ≥ 2026-08-01 (bulletin). PY02: no limit. PY03: 60 days. | PY01, PY03 | Perfect boundaries (PY01 min denied lag 31 / 46; PY03 62). |
| D04 | Harbor T1019 modifier must be `U2`. From DOS ≥ 2026-09-01, Sat/Sun T1019 must be `U3` (bulletin). Missing/U1/wrong U3 → D04. | PY02 only | Perfect. Northwind/Summit modifiers (U1/U2/blank) are never denied. |
| D05 | A `frequency_code=1` claim whose patient + service + DOS + payer matches **any earlier-submitted claim** (any outcome, either file) is a duplicate. Corrected claims (`7`) are exempt. | All | Perfect (27/27). |
| D07 | `auth_id` empty. | All | Perfect. |
| D08 | Auth exists but patient / service / payer / date range doesn't match the claim. Only date-range mismatches occur in the data. | All | Perfect. |
| D10 | **Summit contract amendment 3** (not in the manual, only in remarks): for DOS ≥ **2026-07-01**, PY03 units must be a multiple of 4 (whole hours). Applies to **all agencies**. | PY03 only | June DOS with non-hourly units paid even when submitted in July; every July+ DOS gets D10/D99. |
| D02 | Units > remaining units on the auth. **Ledger of paid units drawn down in `submitted_date` order.** | All | See the stateful ledger section below. |
| D06 | Harbor: member's paid units across **all services** on a DOS can't exceed 32. Stateful (paid total so far that day). | PY02 only | 79 D06s, all PY02. Not yet validated with a ledger. |

### Denial precedence (only one code reported), confirmed with a prototype engine and ledger
| Payer | Order (first match wins) |
|---|---|
| PY01 Northwind | D01 > D09 > D07 > D08 > D02 > D03 > D05 |
| PY02 Harbor | D01 > D09 > D07 > D08 > D04 > D05 > D06 > D02 |
| PY03 Summit | D01 > D07 > D08 > D10 > D09 > D02 > D03 > D05 |

Prototype back-test with this order (rules set on the full history, no holdout yet): **98.1% exact match** on 3,830
claims, **99.7%** excluding the 61 generic D99s. Every non-D99 code is 100% right. The only misses: 12 amendment-3 D99s
(predicted D10, really a labelling issue) and 1 paid claim predicted D02 (see "Hidden outcomes" below). Same-day ties
broken by `claim_id`, which works.

### Label noise / unpredictable
- **D99** (73): 61 have generic remarks ("Denied - contact payer", "Under review - see payer correspondence") and no
  rule flags them, so they look random/manual. 12 more D99s have the amendment-3 remark and are really D10s labelled D99.
  Expect a ceiling below 100% accuracy (~1.6% of history).
- D02 remarks vary ("Authorization exhausted" / "Units exceed remaining authorization") but mean the same thing.
- D01 and D08 also have two remark variants each.

## The stateful ledger (critical)
- `claims_to_check` submission dates (2026-08-24 → 2027-02-17) **overlap** history (2026-06-02 → 2027-01-31).
  318 of 523 to-check claims with a known auth were submitted *before* some history claim on the same auth.
- **History outcomes were generated with the to-check claims already in the ledger.** Example: AUTH10782 has 32
  units. To-check C020563 (16u, submitted 10-08) was paid, so history C010286 (24u, submitted 10-15) got D02 even though
  history alone shows 32 units remaining. Same pattern for AUTH10744, AUTH10768 and AUTH11534.
- So the engine must **merge both files into one timeline sorted by `submitted_date`** and run one ledger:
  - Every claim predicted/known as **paid** draws its units from its auth (D02 state) and adds to the member's
    daily total for PY02 (D06 state).
  - Every submitted claim (any outcome) goes into the duplicate index (D05 state).
- A simple drawdown using only history-paid claims gets 53/65 D02s right. The 12 misses are explained by to-check
  claims that were paid earlier on the same auth.
- Same-day tie-break on `submitted_date` still to settle (`claim_id` order is the working assumption).
- Back-testing is subtle: to replay history accurately the ledger needs the to-check claims' (predicted) outcomes, and
  vice versa. Use known history outcomes for history rows and predicted outcomes for to-check rows.

## Scan of all claims (prototype run, 2026-11-24)
- **Predicted for the 546 to-check claims:** 377 paid, D01 28, D05 27, D03 26, D09 24, D06 18, D10 17, D02 13,
  D07 9, D08 5, D04 2.
- **Multi-issue claims:** 29 to-check claims break 2+ rules, but the payer reports one. Fixing the reported one would
  just surface the next. Example: corrected claim C021766 fixed its D07 but still fails D09 and D10, which were hidden
  by Summit's precedence.
- **Corrected claims:** 12 of 14 fix their original problem. C021766 (D10) and C021797 (auth exhausted, D02) still fail.
- **Duplicates (27):** every one has the same units as the original. 18 originals were already paid (double billing:
  just don't send). 7 duplicate another to-check claim. 2 duplicate a D99 denial (should have been a frequency-7 correction).
- **D07/D08:** a valid authorization for that patient, service, payer and date **always exists** (145/145 history,
  14/14 to-check). So the fix is fully deterministic: "use AUTHxxxxx".
- **D01 splits three ways:** no visit at all (19), visit exists but `rejected`/`manual_edit_pending` (9, which a coordinator can
  chase), and 7 with a verified visit on the **adjacent day** (possible wrong date of service, needs a person).
- **D06:** 12 of 18 are a single claim above 32 units by itself. The fix is to cap units.
- **Hidden outcomes:** the history ledger implies to-check C021301 was *denied*, though all rules pass. History paid
  C012372 on AUTH11084 only works if C021301 didn't draw 8 units. History can occasionally reveal to-check outcomes.
- **Billing rates are fixed per code** (T1005 $4.80, S5125 $5.40, T1019 $6.10 per unit), and all claims are
  consistent. A cheap sanity check, but not a denial code.
- **Not used by any rule:** `caregiver_id`, `agency_id` (amendment 3 applies to every agency). To-check units go down to 4
  (history minimum 6); no rule cares.

## Corrected claims in `claims_to_check`
- 14 claims with `frequency_code=7`, all `original_claim_id`s exist in history. Example: C021802 resubmits C011312
  (D10, 7 units) as 4 units. They're judged on their own merits (not D05). Whether they draw on the auth like any paid
  claim is assumed yes.

## Open questions / to decide in design
1. ~~Position of D02/D06~~ and ~~same-day tie-break~~: resolved (see the precedence table).
2. Precedence was learned on the full history, so re-check it on a time holdout.
3. Whether a D09 cap or D02 remaining counts units from a corrected claim's denied original (assumed no: denied
   claims draw nothing).
4. How to present D99 (predict "paid" vs flag for human review).
5. What the AI does vs the rules (e.g. turning manuals/bulletins/remarks into rule config, explaining fixes), and where
   the human stays in the loop.
