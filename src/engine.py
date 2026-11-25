"""Walk every claim in submission order, check it, and keep the ledger up to date.

History and to-check claims are one timeline: their submission dates overlap, and the
history outcomes only add up if the to-check claims draw units from the same
authorizations (see CLAUDE.md, "The stateful ledger").
"""

from .fixes import plan_fixes
from .ledger import Ledger
from .load import Dataset
from .models import CheckResult
from .rules import RuleContext, VisitIndex, check_claim, reported_issue


def run(dataset: Dataset, payer_rules: dict[str, dict]) -> list[CheckResult]:
    """Check every claim. Returns one CheckResult per claim, in submission order.

    For the ledger, history claims use their KNOWN outcome (what actually happened);
    to-check claims use the PREDICTED outcome as submitted (before any fixes).
    """
    ctx = RuleContext(visits=VisitIndex(dataset.visits), auths=dataset.auths, payer_rules=payer_rules)
    ledger = Ledger(dataset.auths)
    by_id = {c.claim_id: c for c in dataset.claims}
    results = []

    for claim in sorted(dataset.claims, key=lambda c: (c.submitted_date, c.claim_id)):
        issues = check_claim(claim, ctx, ledger)
        reported = reported_issue(issues, payer_rules[claim.payer_id]["precedence"])
        result = CheckResult(claim=claim, issues=issues, predicted_code=reported.code if reported else "")

        if claim.source == "to_check":
            plan_fixes(result, ctx, ledger)
            note_on_correction(result, by_id)
            paid = not reported
        else:
            paid = claim.actual_status == "paid"

        ledger.record(claim, paid)
        results.append(result)

    return results


def note_on_correction(result: CheckResult, by_id: dict) -> None:
    """For a corrected claim, say whether the original's problem is now fixed."""
    claim = result.claim
    if claim.frequency_code != "7":
        return
    original = by_id.get(claim.original_claim_id)
    if original is None or not original.actual_code:
        return
    still_there = any(i.code == original.actual_code for i in result.issues)
    status = "still present" if still_there else "fixed"
    result.notes.append(f"Correction of {original.claim_id}: original problem {original.actual_code} is {status}")
