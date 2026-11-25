# Pre-submission claim checker

Checks home-care claims **before they're submitted**, predicts whether each payer will pay or deny them (and with
which denial code), applies the obvious fixes automatically, and sends everything ambiguous to a person through a
Discord bot (a stand-in for Slack).

> **Rules check the claims and apply the obvious fixes. The AI helps maintain the rules and explain them. People only
> handle what's ambiguous.**

## Results on the synthetic data

| | |
|---|---|
| Back-test on 3,830 past claims | **98.1%** exact match (status and denial code) |
| Excluding the 61 generic "contact payer" D99 denials no rule can predict | **99.7%** |
| Every other denial code (D01–D10) | 100% |

For the 546 claims to check (`output/predictions.csv`):

| Outcome | Claims |
|---|---|
| Pass as submitted | 377 |
| Auto-fixed (modifier and duplicate fixes only, then re-checked) | 4 ($320.80 now payable) |
| Don't submit (duplicate of a claim already paid) | 27 |
| Needs a person (unit cuts come with the exact suggested number) | 138 ($14,707.40 at risk) |

## How it works

```
 data files (read-only)
        │
        ▼
 1. Load and clean ──── can't read it? ───▶ #data-issues (person approves a spelling → mappings.yaml)
        │
        ▼
 2. Rule engine + ledger (rules/PY0x.yaml) ──▶ 3. Fixes: auto (approved list) or person ──▶ #claim-review
        │
        ▼
 4. Back-test vs history ── misses ──▶ 5. LLM groups them, proposes rules ──▶ #rule-proposals
                                          (tested on history first)           #needs-investigation
```

1. **Load and clean** (`src/load.py`). Known problems are fixed at runtime and logged: 9 spellings of 3 payers, two
   date formats, stray whitespace and case, and 32 duplicated history rows. **Anything not recognised is held, never
   guessed**, and posted to a person with the closest known value as a suggestion.
2. **Rule engine and ledger** (`src/rules.py`, `src/ledger.py`, `src/engine.py`). History and to-check claims form **one
   timeline in submission order**. A running ledger tracks units left on each authorization, Harbor's paid units per
   member per day, and every claim seen (for duplicates). Each claim runs through every rule, and the payer's
   precedence order picks the one code it would report. Payer rules live in YAML, so an approved rule goes live
   without code changes.
3. **Fixes** (`src/fixes.py`). Every problem on a claim gets a fix. **Approved auto fixes**: modifier (D04), drop a duplicate
   of a paid claim, and turn a duplicate of a denied claim into a correction (D05). **Unit cuts (D09 visit time, D06
   daily cap, D10 whole hours) go to a person**: cutting gives up revenue and the visit record may be wrong. The person
   gets the exact suggested number, worked out by applying every unit fix and re-checking (C019037: 20 → 16). A claim is only auto-fixed if every problem has an auto fix,
   the billed amount doesn't go up, and the fixed claim **passes every rule when checked again**. **Authorization fixes
   (D07/D08) always go to a person**, with the valid authorization suggested.
4. **Back-test** (`src/backtest.py`). Replays history and writes `output/backtest_report.md`.
5. **Rule proposals** (`src/propose.py`, LLM). Claims we got wrong go to Claude Sonnet 5.5, which groups them and
   proposes rules in a fixed set of rule types the engine can run. Every proposal is **re-tested on the full history**
   (denials it fixes, paid claims it would wrongly deny) before a person sees it. Groups with no pattern go to
   #needs-investigation, and people's findings are fed back into the next round.

**What's AI and what's rules.** No LLM decides whether a claim is paid. Claude Haiku 5.5 only rewords the engine's
findings into plain English (`src/explain.py`), with a template fallback. Claude Sonnet 5.5 only *proposes* rules,
which people approve.

## Running it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip

.venv/Scripts/python run.py check                              # back-test + predictions, writes output/
.venv/Scripts/python run.py check --extra demo/odd_claims.csv  # adds 4 badly formatted claims (hold path)
.venv/Scripts/python run.py explain C021766                    # explanation for one claim
.venv/Scripts/python -m pytest -q                              # 20 tests

# Keys: copy your keys into .env (git-ignored; template in .env.example)

# LLM features (needs ANTHROPIC_API_KEY)
.venv/Scripts/python run.py propose --without-contract-rules   # demo: rediscover Summit amendment 3

# Discord (needs DISCORD_TOKEN; create channels claim-review, data-issues, rule-proposals, needs-investigation)
.venv/Scripts/python run.py bot --extra demo/odd_claims.csv    # then type !post in Discord
```

Outputs in `output/`:
- `predictions.csv`: each claim as submitted, before fixes
- `corrected_claims.csv`: auto-fixed claims, with what changed and why
- `review_queue.csv`: every claim that isn't a clean pass, with all its problems and fixes
- `data_issues.csv`: held claims
- `backtest_report.md`
- `decisions.jsonl`: every reaction and reply in Discord

## Assumptions

- **Both claim files are one timeline.** History outcomes only add up if to-check claims draw units from the same
  authorizations. For example, AUTH10782's D02 on C010286 depends on to-check C020563 being paid first.
- Only paid claims draw down units. To-check claims use their *predicted* outcome as submitted; history claims use
  the known outcome. Same-day submissions are processed in `claim_id` order.
- A visit belongs to its clock-in date, and same-day visits are added together. Only `verified` visits count.
- `MM/DD/YYYY` is US order: the first number is never above 12 in the data. A new ambiguous format would be held.
- **Precedence is per payer and learned from history.** Summit reports authorization problems before units over visit
  time; Northwind and Harbor do the opposite.
- **Summit contract amendment 3** (whole hours from 1 Jul 2026, all agencies) isn't in any manual. It was found from
  denial remarks.
- Rules were tuned on the full history, so the back-test overstates how it would do on new data. The report splits
  results by period to show drift.
- **Not tested live:** I had no API key or Discord token while building this. The LLM calls follow the Anthropic SDK
  docs and fall back to templates when unavailable. `propose`'s rule-testing logic is unit-tested with a faked LLM
  answer, and the bot's message formatting and spelling-approval path are tested offline.

## What I'd do next

- Reserve units for claims still pending at the payer. The ledger currently assumes it knows every outcome.
- A proper time-based holdout: re-learn precedence on early months only.
- Persist the bot's message-to-claim map, so reactions still work after a restart.
- Write corrected claims back to the billing system instead of a CSV.

See `PLAN.md` for the design, `CLAUDE.md` for the data findings, and `PROMPT_LOG.md` for how this was built with
Claude Code.
