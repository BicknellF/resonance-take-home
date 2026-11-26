"""Run the whole agent once, and compare two runs (used by `run.py` and the Discord bot).

Every human decision is saved as an edit and the whole timeline runs again, so one
edit can move other claims through the ledger. `changed()` finds those knock-ons.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import engine
from .config import load_payer_rules
from .load import Dataset, load_dataset
from .models import CheckResult


@dataclass
class Run:
    dataset: Dataset
    rules: dict
    results: list[CheckResult]

    def to_check(self, outcome: Optional[str] = None) -> list[CheckResult]:
        return to_check(self.results, outcome)

    def result(self, claim_id: str) -> Optional[CheckResult]:
        return next((r for r in self.results if r.claim.claim_id == claim_id), None)

    def holds_for(self, claim_id: str):
        return [h for h in self.dataset.holds if h.claim_id == claim_id]

    def snapshot(self) -> dict[str, str]:
        """Each to-check claim's state: "outcome code" (e.g. "needs_person D02") or "held"."""
        state = {h.claim_id: "held" for h in self.dataset.to_check_holds}
        for r in self.to_check():
            state[r.claim.claim_id] = f"{r.outcome} {r.predicted_code}".strip()
        return state

    def status(self, claim_id: str) -> str:
        """One line on where a to-check claim stands now (shared by the CLI and the bot)."""
        holds = self.holds_for(claim_id)
        if holds:
            return "On hold: " + "; ".join(f"{h.field} `{h.value}` ({h.reason})" for h in holds)
        result = self.result(claim_id)
        return result.outcome_text if result else "Not found."


def run(extra_claims_file: Optional[Path] = None, edits: Optional[dict] = None,
        without_contract_rules: bool = False) -> Run:
    """Load (with a person's edits applied before cleaning), check every claim, return the run."""
    dataset = load_dataset(extra_claims_file, edits)
    rules = load_payer_rules(without_contract_rules=without_contract_rules)
    return Run(dataset, rules, engine.run(dataset, rules))


def to_check(results: list[CheckResult], outcome: Optional[str] = None) -> list[CheckResult]:
    """The to-check claims (not history), optionally only those with one outcome."""
    return [r for r in results if r.claim.source == "to_check" and (outcome is None or r.outcome == outcome)]


def changed(before: Run, after: Run) -> dict[str, tuple]:
    """To-check claims whose state differs between two runs: {claim_id: (before, after)}."""
    old, new = before.snapshot(), after.snapshot()
    return {cid: (old.get(cid), new.get(cid)) for cid in sorted(old.keys() | new.keys())
            if old.get(cid) != new.get(cid)}
