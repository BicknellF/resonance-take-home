"""Walk every claim in submission order, check it, and keep the ledger up to date.

History and to-check claims are one timeline: their submission dates overlap, and the
history outcomes only add up if the to-check claims draw units from the same
authorizations (see CLAUDE.md, "The stateful ledger").
"""

from .fixes import UNEXPLAINED_CODES, plan_fixes
from .ledger import Ledger
from .load import Dataset
from .models import CheckResult
from .overrides import NO_AUTO_FIX, describe
from .rules import RuleContext, VisitIndex, check_claim, reported_issue


def run(dataset: Dataset, payer_rules: dict[str, dict], disabled: frozenset = frozenset()) -> list[CheckResult]:
    """Check every claim. Returns one CheckResult per claim, in submission order.

    Two passes over the same timeline:
      1. As submitted: to-check claims draw on the ledger only if they'd be paid as they
         are. This is what the payer saw, so history rows (the back-test) come from here.
      2. The batch as it will be sent: auto-fixed claims are booked as paid in their fixed
         form, so a later claim can't pass a limit (Harbor's 32/day, an authorization's
         units) that an auto fix has already used up. To-check rows come from here, and
         any claim whose result changed because of an earlier fix says so in its notes.
    `disabled` switches denial codes off (ablation study only).
    """
    as_submitted = run_once(dataset, payer_rules, disabled, book_fixes=False)
    if not any(r.outcome == "auto_fixed" for r in as_submitted):
        return as_submitted
    as_sent = run_once(dataset, payer_rules, disabled, book_fixes=True)
    merged = []
    for first, second in zip(as_submitted, as_sent):
        if first.claim.source != "to_check":
            merged.append(first)
            continue
        if (first.outcome, first.predicted_code) != (second.outcome, second.predicted_code):
            second.notes.append(f"Changed when re-checked with the batch's auto fixes booked "
                                f"(was {first.outcome} {first.predicted_code})".replace(" )", ")"))
        merged.append(second)
    return merged


def run_once(dataset: Dataset, payer_rules: dict[str, dict], disabled: frozenset,
             book_fixes: bool) -> list[CheckResult]:
    """One walk over the timeline. History claims use their KNOWN outcome in the ledger;
    to-check claims use the PREDICTED outcome (as submitted, or as fixed if `book_fixes`)."""
    ctx = RuleContext(visits=VisitIndex(dataset.visits), auths=dataset.auths, payer_rules=payer_rules,
                      disabled=frozenset(disabled))
    ledger = Ledger(dataset.auths)
    seen: dict[str, tuple] = {}      # claim_id -> (claim, paid, code), for corrections
    results = []

    for claim in sorted(dataset.claims, key=lambda c: (c.submitted_date, c.claim_id)):
        issues = check_claim(claim, ctx, ledger)
        reported = reported_issue(issues, payer_rules[claim.payer_id]["precedence"])
        result = CheckResult(claim=claim, issues=issues, predicted_code=reported.code if reported else "")
        booked = claim

        if claim.source == "to_check":
            edit = dataset.edits.get(claim.claim_id)
            if edit:
                result.notes.append(f"Edited by a person: {describe(edit)}")
            plan_fixes(result, ctx, ledger, allow_auto=not (edit or {}).get(NO_AUTO_FIX))
            check_correction(result, seen)
            paid, code = not reported, result.predicted_code
            if book_fixes and result.outcome == "auto_fixed":
                booked, paid, code = result.fixed_claim, True, ""
        else:
            paid, code = claim.actual_status == "paid", claim.actual_code

        ledger.record(booked, paid, code)
        seen[claim.claim_id] = (claim, paid, code)
        results.append(result)

    return results


def check_correction(result: CheckResult, seen: dict) -> None:
    """A corrected claim (frequency 7) only goes out without a person if it provably fixes
    a denied original for the same visit. Otherwise it goes to a person, with the reason."""
    claim = result.claim
    if claim.frequency_code != "7":
        return
    problems = []
    original_id = claim.original_claim_id
    if original_id not in seen:
        problems.append(f"original {original_id or '(none given)'} not found among earlier claims")
    else:
        original, paid, code = seen[original_id]
        if original.duplicate_key != claim.duplicate_key:
            problems.append(f"{original_id} is for a different patient, service, date or payer")
        if paid:
            problems.append(f"{original_id} was paid: a correction would replace a paid claim")
        elif code in UNEXPLAINED_CODES:
            problems.append(f"{original_id} was denied {code or 'without a code'} with no stated reason, "
                            f"so nothing shows this correction fixes it")
        else:
            status = "still present" if any(i.code == code for i in result.issues) else "fixed"
            result.notes.append(f"Correction of {original_id}: original problem {code} is {status}")
    if not problems:
        return
    result.notes.append("Correction not verified: " + "; ".join(problems))
    if result.outcome in ("pass", "auto_fixed"):
        result.outcome = "needs_person"
        result.fixed_claim = None
