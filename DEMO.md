# Demo script (10 minutes)

All commands run from the repo root. Activate the virtualenv first so `python` is the project's Python:
`.venv\Scripts\Activate.ps1` (PowerShell) or `source .venv/Scripts/activate` (Git Bash).

## Before the demo

```bash
# once: environment + keys
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cp .env.example .env        # fill in ANTHROPIC_API_KEY and DISCORD_TOKEN

# sanity check (should print "30 passed")
python -m pytest -q
```

- Discord server has the channels `#claim-review`, `#data-issues`, `#rule-proposals`, `#needs-investigation`.
- Have the README open on GitHub (diagrams, confusion matrix, ablation table) and `output/review_queue.csv` ready.
- Terminal font big enough to read, and the window wide enough for the tables.

## Reset (run before the demo, and again after any rehearsal)

The demo changes a few files: people's edits, Discord decisions, LLM proposals, and possibly the rules or mappings
if you click ✅ in Discord. This puts everything back to the committed state:

```bash
git checkout -- rules/ mappings.yaml output/
rm -f output/claim_edits.json output/decisions.jsonl output/findings.jsonl output/rejected_rules.json
python run.py check                        # regenerates output/ (should print 98.1%)
git status --short                         # should be empty
```

## Running order

| # | Time | Command | What to point at |
|---|---|---|---|
| 1 | 1 min | *(README on GitHub)* | The problem: denials are found weeks later and cost money. Pre-submission check. Show the two diagrams. |
| 2 | 2 min | `python run.py check` | Cleaning log (9 payer spellings, mixed dates, 32 duplicate rows). **98.1%** back-test (99.7% excl. generic D99). The 546 claims: 377 pass, 4 auto-fixed, 27 don't send, **46 back to the submitter, 92 to a reviewer (17%)**. |
| 3 | 1 min | `python run.py explain C021766` | A corrected claim that fixed its D07 but still breaks D09 + D10 (hidden by Summit's precedence). All problems kept, suggested 21 → 20. Haiku rewords it (add `--no-llm` if the API is slow). |
| 4 | 1.5 min | `python run.py edit C019627 auth=AUTH13920` | A person's fix goes back through the agent. C019627 now passes, **and the ledger knock-on turns C019010 into a D02** (the auth is now used up). Then `python run.py edit C019627 --clear`. |
| 5 | 0.5 min | `python run.py edit C019037 units=17` | A person's value is checked like the agency's: 17 breaks Summit's whole-hours rule, so it suggests 16. Then `python run.py edit C019037 --clear`. |
| 6 | 1 min | `python run.py check --extra demo/odd_claims.csv` | 4 badly formatted claims (misspelled payer, `13-09-2026`, `T1019-A`, `02/30/2026`) are **held, never guessed**. "Held for a person: 4 claims". |
| 7 | 1.5 min | `python run.py propose --without-contract-rules` | Sonnet rediscovers Summit amendment 3 from the remarks: 35 denials caught, 0 false denials, tested on history before anyone sees it. Backup if the API fails: `output/proposals.json` and the README section. |
| 8 | 1.5 min | `python run.py bot`, then type `!post 3` in Discord | Cards with ✅ / ❌ / 🔍 already added. Click 🔍 (explanation in a thread), ✅ a suggested fix (re-check + knock-on reply), show the rule proposal card. |

After step 6, run `python run.py check` again so `output/predictions.csv` is back to the 546 claims.
After the demo, run the reset steps.

Notes:
- Step 7 overwrites `output/proposals.json` and `output/investigations.json`. The reset puts the committed versions back.
- In step 8, ✅ on the amendment-3 proposal replies "rule already exists", because the YAML still has it
  (`--without-contract-rules` only removes it for that run). That's expected.
- `python run.py ablate` takes a while. Show the README ablation table instead of running it live.

## What to make sure I cover

**The problem and framing**
- Denials cost money and are found weeks after submission. Checking *before* submission prevents them.
- Brief: frame a problem, design the system (AI vs rules, human in the loop), build it, prove it with a back-test.

**Key findings from the data**
- Every coded denial (D01–D10) follows a deterministic rule, confirmed against history.
- Summit amendment 3 isn't in any manual. It was found in the denial remarks.
- Bulletins change rules mid-period (Northwind 45 days from Aug, Harbor weekend U3 from Sep).
- **The two claim files are one timeline.** History outcomes only add up if to-check claims drew auth units first
  (C020563 paid → C010286 got D02).
- The payer reports only one code: precedence was learned from history, but all problems are kept.

**Design decisions**
- **Rules decide, people approve, the LLM explains and proposes.** The LLM never decides an outcome.
- Why not an LLM per claim: exact, auditable rules already get 100% on every coded denial. An LLM would add cost,
  delay and nondeterminism.
- Stateful ledger: auth units, Harbor's daily cap, every claim seen (duplicates).
- Cleaning at runtime, source files never touched. Unknown values are held, never guessed.
- Payer rules live in YAML, so a rule change needs no code change.

**Human in the loop**
- **75% of claims need no one, 8% go back to the submitter, 17% go to a reviewer**, each with the exact fix suggested.
- Auto fixes only where no money changes (modifier, duplicates, frequency 7), and only if the claim passes again.
- Every human edit goes back through the agent: the whole timeline is re-checked and knock-on effects reported.

**Results**
- 98.1% exact match, 99.7% excluding generic D99s, 1 false denial in 2,731 paid claims.
- The ceiling is about 98.4%: 61 D99s with "contact payer" remarks look like manual decisions.
- Ablation: manuals 89% → + bulletins → + amendment 3 → + ledger → + timeline → + precedence = 98.1%.
  The 8-minute rule alone is worth 14.8 points.
- Sonnet rediscovered amendment 3 with 0 false denials, tested on history before a person approved it.

**Honest limitations**
- Rules and precedence were tuned on the full history, so 98.1% overstates accuracy on new data.
- The ledger assumes every outcome is known. In real life, claims pending at the payer should reserve units.
- History hints that to-check C021301 was really denied (the "hidden outcome").

**What I'd do next**
- A real time holdout, reserving units for pending claims, writing fixes back to the billing system, and
  persisting the bot's state across restarts.
