"""Decide how to fix each issue, and apply the approved automatic fixes.

Approved auto fixes (user decision, see PLAN.md):
  D04 set modifier · D05 drop a duplicate of a paid claim ·
  D05 turn a duplicate of a denied claim into a correction (frequency 7)

Never automatic, a person decides (the system suggests the exact change):
  - Unit cuts (D09 visit time, D06 daily cap, D10 whole hours). Cutting gives up revenue,
    and the visit record itself may be wrong, so it's a judgement call.
  - Authorization fixes (D07/D08).

Guardrails: a claim is only auto-fixed if every issue has an auto fix, the billed
amount doesn't go up, and the fixed claim passes every rule when checked again.
"""

from dataclasses import replace

from .ledger import Ledger
from .models import CheckResult, Claim, Fix, Issue
from .rules import RuleContext, check_claim

# Fixes are applied in this order: caps first, contract rounding (D10 and any code not
# listed) last, so a suggested unit count satisfies every rule.
APPLY_ORDER = ["D05", "D04", "D09", "D06", "D10"]
MAX_FIX_ROUNDS = 3


def with_units(claim: Claim, units: int) -> Claim:
    """Change units and recompute the billed amount at the same rate."""
    return replace(claim, units=units, billed_amount=round(units * claim.rate_per_unit, 2))


def fix_for(issue: Issue, claim: Claim) -> Fix:
    """The fix for one issue.

    `auto=True` fixes are applied without asking. Fixes with an `apply` function but
    `auto=False` (unit cuts) are only used to suggest the corrected claim to a person.
    """
    d = issue.details

    if issue.code == "D04":
        return Fix("D04", f"Change modifier to {d['required']}", auto=True,
                   apply=lambda c: replace(c, modifier=d["required"]))

    if issue.code == "D09":
        cap = d["units_supported"]
        return Fix("D09", f"Bill at most {cap} units (verified {d['verified_minutes']} min), "
                          f"or correct the visit record if it's wrong", auto=False,
                   apply=lambda c: with_units(c, min(c.units, cap)))

    if issue.code == "D06":
        left = d["remaining_today"]
        return Fix("D06", f"Bill at most {left} units (what's left of the 32/day cap), "
                          f"or decide which service that day to reduce", auto=False,
                   apply=lambda c: with_units(c, min(c.units, left)))

    if "rule" in d:   # contract rules from the payer's YAML (e.g. Summit amendment 3, D10)
        rule = d["rule"]
        if rule["type"] == "units_multiple_of":
            m = rule["value"]
            return Fix(issue.code, f"Round units down to a multiple of {m} (whole hours)", auto=False,
                       apply=lambda c: with_units(c, c.units // m * m))
        if rule["type"] == "max_units_per_claim":
            cap = rule["value"]
            return Fix(issue.code, f"Bill at most the contract maximum of {cap} units", auto=False,
                       apply=lambda c: with_units(c, min(c.units, cap)))

    if issue.code == "D05":
        original = d["original_claim_id"]
        if d["original_paid"]:
            return Fix("D05", f"Don't submit: {original} for the same visit was already paid",
                       auto=True, stop_claim=True)
        return Fix("D05", f"Resubmit as a correction of {original} (frequency code 7)", auto=True,
                   apply=lambda c: replace(c, frequency_code="7", original_claim_id=original))

    if issue.code in ("D07", "D08"):
        valid = d.get("valid_auths", [])
        if len(valid) == 1:
            hint = f"use {valid[0]}"
        elif valid:
            hint = f"one of {', '.join(valid)}"
        else:
            hint = "no valid authorization on file: request one from the payer"
        return Fix(issue.code, f"Check the authorization number ({hint})", auto=False)

    if issue.code == "D01":
        if d.get("unverified_statuses"):
            statuses = ", ".join(d["unverified_statuses"])
            return Fix("D01", f"A visit exists but isn't verified ({statuses}): chase the approval, then submit",
                       auto=False)
        if d.get("verified_days_near"):
            near = ", ".join(d["verified_days_near"])
            return Fix("D01", f"No visit that day, but a verified visit on {near}: is the date of service wrong?",
                       auto=False)
        return Fix("D01", "No visit recorded: don't bill until the visit is verified (possible compliance issue)",
                   auto=False)

    if issue.code == "D02":
        return Fix("D02", f"Authorization has {d['remaining']} units left: request a new authorization",
                   auto=False)

    if issue.code == "D03":
        return Fix("D03", "Past the filing limit: can't be fixed (write off or appeal)", auto=False)

    return Fix(issue.code, "No automatic fix known: needs a person", auto=False)


def plan_fixes(result: CheckResult, ctx: RuleContext, ledger: Ledger) -> CheckResult:
    """Fill in fixes and the outcome for a checked claim. Call before the ledger records it."""
    claim, issues = result.claim, result.issues
    if not issues:
        result.outcome = "pass"
        return result

    result.fixes = [fix_for(issue, claim) for issue in issues]

    if any(f.stop_claim for f in result.fixes):
        result.fixes = [f for f in result.fixes if f.stop_claim]   # other fixes are moot
        result.outcome = "do_not_submit"
        return result

    if not all(f.auto for f in result.fixes):
        result.outcome = "needs_person"
        suggest_correction(result, ctx, ledger)
        return result

    fixed, result.fixes, remaining = apply_fixes(claim, result.fixes, ctx, ledger, allowed=lambda f: f.auto)
    if fixed.units <= 0:
        result.notes.append("Auto fix would leave 0 units")
        result.outcome = "needs_person"
    elif fixed.billed_amount > claim.billed_amount:
        result.notes.append("Auto fix would increase the billed amount")
        result.outcome = "needs_person"
    elif remaining:
        result.notes.append("Still fails after auto fixes: " + ", ".join(i.code for i in remaining))
        result.outcome = "needs_person"
    else:
        result.fixed_claim = fixed
        result.outcome = "auto_fixed"
    return result


def apply_fixes(claim: Claim, fixes: list[Fix], ctx: RuleContext, ledger: Ledger, allowed):
    """Apply fixes, check again, and repeat while every new issue has an `allowed` fix.

    A fix can expose a new issue (cutting units to the visit time can break Summit's
    whole-hours rule), hence the repeat. Returns (fixed claim, all fixes used, issues left).
    """
    fixed, used, remaining = claim, list(fixes), []
    for _ in range(MAX_FIX_ROUNDS):
        for fix in sorted(fixes, key=lambda f: APPLY_ORDER.index(f.code) if f.code in APPLY_ORDER else 99):
            fixed = fix.apply(fixed)
        remaining = check_claim(fixed, ctx, ledger)
        fixes = [fix_for(issue, fixed) for issue in remaining]
        if not remaining or not all(allowed(f) and f.apply for f in fixes):
            break
        used += fixes
    return fixed, used, remaining


def suggest_correction(result: CheckResult, ctx: RuleContext, ledger: Ledger) -> None:
    """For a claim going to a person, work out the corrected claim the suggested fixes would give.

    Nothing is changed: the suggestion is added as a note, for the person to accept or not.
    """
    claim = result.claim
    suggestable = [f for f in result.fixes if f.apply]
    if not suggestable:
        return
    fixed, _, remaining = apply_fixes(claim, suggestable, ctx, ledger, allowed=lambda f: True)
    changes = [f"{name} {getattr(claim, name) or 'none'} -> {getattr(fixed, name)}"
               for name in ("units", "modifier", "frequency_code") if getattr(claim, name) != getattr(fixed, name)]
    if not changes:
        return
    outcome = "would then pass" if not remaining else f"still needs: {', '.join(sorted(set(i.code for i in remaining)))}"
    result.notes.append(f"Suggested correction: {'; '.join(changes)} (${fixed.billed_amount:.2f}); {outcome}")
