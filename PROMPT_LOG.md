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
