# Prompt Log

A running log of prompts given to Claude Code in this project, with a short summary of each response.

---

## 1. 2026-11-24

**Prompt:**
> please make an MD file that logs my prompts and a bullet point summary of claude response

**Claude response:**
- Created `PROMPT_LOG.md` (this file) to record each prompt and a bullet-point summary of the response.
- Added a `CLAUDE.md` instruction so Claude appends a new entry after every prompt in future turns and sessions.

---

## 2. 2026-11-24

**Prompt:**
> ok to start this project have a read of all the documents. I read through them and think we should design an "agent" that uses deterministic rules to check each claim, remember to make sure it has running tracking of the units autorised as this is staeful not stateless. write down your findings in CLAUDE.md before making desing decisons. It also looks liek we will ahve to do some data cleaning but that should be done at runtime not inplace

**Claude response:**
- Read both PDFs, the README, billing basics, denial codes and all payer manuals and bulletins, and profiled all 4 CSVs (exploratory scripts kept in the scratchpad; the data files were not modified).
- Found the data issues to clean at runtime: 3 payer-name variants per payer, mixed ISO and MM/DD/YYYY dates, whitespace and case problems in service codes and modifiers, and 32 exact duplicate rows in history.
- Checked each denial rule against history. D01, D03, D04, D05, D07, D08, D09 and D10 all matched with no false denials. Found a hidden Summit "amendment 3" whole-hour rule (DOS ≥ 2026-07-01, all agencies) that appears only in remarks.
- Key finding: history outcomes were generated with the to-check claims in the same authorization ledger, so the engine must merge both files in submission order. Precedence is payer-specific (D07/D08 beat D09 only for Summit).
- Wrote all findings, the precedence evidence, D99 label noise and open questions into `CLAUDE.md`. No design decisions made yet.

---

## 3. 2026-11-24

**Prompt:**
> ok my mental model is that we can implement deterministic tests and cleaning steps on the data and detect any bad claims without needing an LLM. do you follow? is there anywhere you would push back?

**Claude response:**
- Agreed: the rules reproduce history with no false denials, so detection, denial code and most fixes can be fully deterministic. That's better for auditability, cost and speed.
- Pushed back on framing: the brief asks for an AI assistant. Proposed the LLM's job is maintaining the rules (manuals, bulletins and remarks → rule config, human-approved), not checking claims.
- Flagged overfitting: precedence and the Summit amendment were learned from the same history used to test. Suggested a time-based holdout and an "unexplained denial" monitor to catch rule drift.
- Flagged that the ledger assumes knowledge of all pending claims, plus D99 noise (~1.6%) and fixes that need a human (missing EVV, exhausted auth).

---

## 4. 2026-11-24

**Prompt:**
> ok please draft a plan md of what the solution would look like with deterministic checking, unexplained denial grouping w LLM, and denial -> fix explaination. we can use discord a discord bot as an easy standin for slack here where users react with a tick or cross or can give a text response. I will then review this plan. keep it simple and easy to follow for a human

**Claude response:**
- Wrote `PLAN.md`: problem framing, the "rules check, AI maintains and explains, people approve" idea, an ASCII architecture diagram and 5 components.
- Deterministic core: runtime cleaning, one timeline with a stateful ledger (auth units, Harbor daily cap, duplicates), rules in per-payer YAML with precedence.
- LLM roles: Haiku writes fix explanations (it can't change decisions, and falls back to a template); Sonnet groups unexplained back-test misses into proposed YAML rules, which are tested on history before being posted.
- Discord: #claim-review and #rule-proposals channels with ✅ / ❌ / text-reply behaviour, plus a decision log.
- Build order (core first, cut from the bottom), a time-holdout back-test, assumptions and out-of-scope list.

---

## 5. 2026-11-24

**Prompt:**
> unexplainable denials should probably be flagged to a human. they likely have extra context or could call up someone who does

**Claude response:**
- Updated `PLAN.md`: denials the LLM can't turn into a rule now go to a person instead of being accepted as noise.
- Added a `#needs-investigation` Discord channel with the full context (claim, remark, rules passed, the LLM's best guess). ✅ for resolved, ❌ for can't resolve, or a text reply.
- A person's text finding feeds back into grouping, so it can become a new rule proposal.
- Noted the limit: these denials can't be predicted *before* submission, so they're flagged after the fact and still cap accuracy at ~1.6% below perfect.

---

## 6. 2026-11-24

**Prompt:**
> ok before we finalise this plan please run through all claims and tell me if there is anything there that we havent covered so far with deterministic testing or flagging to humans?

**Claude response:**
- Built a throwaway prototype engine (scratchpad) with the merged timeline and ledger, and pinned down the per-payer precedence including D02/D06. Back-test: 98.1% exact, 99.7% excluding generic D99s, and every non-D99 code 100%.
- Gaps found in the plan: 29 to-check claims break 2+ rules (fixes must list *all* issues, not just the reported code); 2 of 14 corrected claims still fail.
- Fix nuances: duplicates are mostly true double billing (don't send) vs should-be-corrections; D07/D08 always have a valid auth to swap in; D01 splits into no visit / unverified visit / visit on the adjacent day; D06 is often a single claim over 32.
- Found a "hidden outcome": the history ledger implies to-check C021301 was denied even though all rules pass, so it should be flagged to a person.
- Recorded everything in `CLAUDE.md`; `PLAN.md` not yet changed, pending the user's call.

---

## 7. 2026-11-25

**Prompt:**
> please add these gaps to the plan. we should also have human in the loop when something comes up that is not parsed by teh deterministic checks. e.g. weird spellings of names or weird date formats

**Claude response:**
- Rewrote `PLAN.md` (v2) with a "never guess" principle: unrecognised payer spellings, codes, modifiers, unknown date formats, impossible dates, missing fields or unknown IDs put the claim on hold (`needs_data_fix`) instead of being guessed.
- Added a `#data-issues` Discord channel. The bot suggests the closest known value (string matching, no LLM); ✅ adds it to a new `mappings.yaml` and re-runs held claims, and a text reply supplies the right value.
- Added the scan gaps: the engine records *all* problems per claim; a corrected-claim check (original fixed + no newly exposed issue); hidden-outcome flagging; and a per-situation fix table (auto vs person) with a re-run after auto fixes.
- Added the confirmed precedence table and prototype accuracy (98.1% / 99.7%), and updated the diagram, outputs, build order and assumptions.
- Noted the current data has no unreadable values after the known fixes, so the hold path will be demoed with a few deliberately odd claims.

---

## 8. 2026-11-25

**Prompt:**
> ok I have read the plan and all looks good. PLease go and read the docs first to confirm my undesrtanding. we have this claim review discord channel but this is just for observability reasons? the actual changes would ideally be made by the user inputting not the claims handler

**Claude response:**
- Checked against the brief ("check a claim… predict which claims will be denied, and suggest the fix") and billing_basics (the agency submits corrected claims): the assistant suggests, and the billing person makes the change in their own system.
- Agreed the system should never edit claims itself, and suggested #claim-review is better framed as the billing team's work queue plus feedback channel than as pure observability.
- Proposed re-wording ✅/❌ to "will fix / disagree", and closing the loop by re-checking the corrected claim when it comes back in; offered to update `PLAN.md`.

---

## 9. 2026-11-25

**Prompt:**
> the stakes are only high if the error is ambiguous. if it is somethign that is flagged by the deterministic checks and has a deterministic fix we should just apply that

**Claude response:**
- Agreed, and updated `PLAN.md` (v3): claims with only deterministic fixes are auto-fixed. People handle only ambiguous cases (no fix, more than one possible fix, or someone outside the system needed).
- Added guardrails for auto fixes: the fix must be unique, must never increase the billed amount, and the claim must pass when re-run after *all* fixes; otherwise the whole claim goes to a person.
- Auto-fixed claims go to a new `corrected_claims.csv` (source data untouched) and post as an FYI in #claim-review with ❌ to undo; undos are logged as rule feedback.
- `predictions.csv` stays "as submitted" (before fixes) so it can still be compared; added an auto-fixed vs needs-a-person vs dollars summary.

---

## 10. 2026-11-25

**Prompt:**
> please list all of the potential auto fixes. swapping auhorisations seems dangerous so let me decide now which ones can acually be auto fixed

**Claude response:**
- Listed 8 candidate auto fixes with to-check claim counts and risk: D04 modifier, D09 cut to verified, D10 whole hours, D06 daily cap, D05 drop (original paid), D05 convert to correction (original denied), D07 fill missing auth, D08 swap wrong auth.
- The user chose: all unit/modifier fixes and both duplicate fixes are auto; authorization fixes (D07/D08) are **never** auto (a person gets the valid auth as a suggestion).
- Updated `PLAN.md`: an approved auto-fix list in the guardrails, and the D07/D08 row in the fix table set to "Person".

---

## 11. 2026-11-25

**Prompt:**
> ok the plan looks good please implement it. keep the code clean and easy to undesrtand so that I can review it

**Claude response:**
- Implemented the plan: `run.py` CLI plus `src/` modules (load/clean with holds, rules, ledger, engine, fixes, backtest, outputs, explain via Haiku 5.5, propose via Sonnet 5.5, Discord bot, mappings); payer rules in `rules/PY0x.yaml`; spellings in `mappings.yaml`; a project `.venv` (the system Python's anthropic install was broken).
- Back-test: 98.1% exact (99.7% excluding generic D99s), matching the prototype. To-check: 377 pass, 56 auto-fixed, 27 don't submit, 86 need a person, plus outputs in `output/`.
- Fixed a bug found while checking the output: a fix can expose a new issue (cutting to visit time breaks Summit's whole hours), so fixes now repeat until the claim passes (C019037: 20 → 16 units).
- Added `demo/odd_claims.csv` for the hold path and 20 tests (cleaning, rules, back-test, ledger, precedence, auto-fix guardrails, proposal testing with a faked LLM, spelling approval).
- Wrote `README.md`. Flagged that no API key or Discord token was available, so the LLM and bot are untested live (they fall back to templates).

---

## 12. 2026-11-25

**Prompt:**
> explain theat claim cutting logic. please explain all options fo that and te trade offs

**Claude response:**
- Explained the current logic: cut units to the largest value that passes every rule (min of the verified-time cap and the daily-cap remainder, then round down to whole hours for Summit), re-check, and repeat.
- Quantified it: 52 claims cut, 157 units, $837.80 given up. Most cuts are 1–4 units; 8 cuts are over 25% (e.g. C021393 20 → 8 from the Harbor daily cap).
- Set out the options and trade-offs: cut to max compliant (current), round up (rejected: overbilling), never auto-cut, cut with a size threshold, and D06 choices of which claim absorbs the cap (first-come vs highest-rate vs person).
- Recommended keeping round-down plus a threshold (large cuts go to a person), and asked the user to decide before changing code.

---

## 13. 2026-11-25

**Prompt:**
> ok these seem ambiguous so it's definitely worht sending to a human. please use option C and implement

**Claude response:**
- Implemented option C: unit cuts (D09 visit time, D06 daily cap, D10 whole hours) are no longer automatic. Only the modifier (D04) and duplicate (D05) fixes stay automatic.
- A person still gets the exact suggested number: `suggest_correction()` applies every unit fix, re-checks, and adds a note (e.g. C019037 "units 20 -> 16 ($76.80); would then pass"). Nothing is changed automatically.
- Refactored the repeat-until-clean loop into `apply_fixes()`, shared by the auto path and the suggestion path.
- New to-check numbers: 377 pass, 4 auto-fixed, 27 don't submit, 138 need a person. The back-test is unchanged (98.1%).
- Updated the tests (21 pass: unit cuts always go to a person, auto fixes never change units), `PLAN.md` and `README.md`.

---

## 14. 2026-11-25

**Prompt:**
> where is the .env?

**Claude response:**
- There is no `.env` file. The code reads `ANTHROPIC_API_KEY` and `DISCORD_TOKEN` straight from environment variables (`os.environ` in `src/explain.py` and `src/bot.py`); nothing loads a `.env`.
- `.env` is already in `.gitignore`, so one would never be committed.
- Offered to add `.env` support (a `.env.example` template plus loading it at startup) or to explain how to set the variables in the shell.

---

## 15. 2026-11-25

**Prompt:**
> please create the .env and i can add my keys

**Claude response:**
- Created `.env` (git-ignored, empty `ANTHROPIC_API_KEY=` and `DISCORD_TOKEN=` for the user to fill in) and `.env.example` (safe-to-commit template).
- Added `load_env()` in `src/config.py`, called at the start of `run.py`. It skips blank values, strips quotes, and never overrides variables already set in the shell. No new dependency.
- Verified with a throwaway file, confirmed git ignores `.env`, and updated the README run instructions. Tests still pass.
