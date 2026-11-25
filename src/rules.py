"""The deterministic rule checks, one function per denial code.

Each check looks at one claim (plus the visits, authorizations, the payer's YAML rules
and the ledger) and returns an `Issue` if the claim breaks the rule, otherwise None.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .ledger import Ledger
from .models import Authorization, Claim, Issue, Visit


class VisitIndex:
    """Fast lookups over EVV visits, keyed by (patient, service, visit date)."""

    def __init__(self, visits: list[Visit]):
        self.verified_minutes: dict[tuple, int] = defaultdict(int)
        self.statuses: dict[tuple, set[str]] = defaultdict(set)
        for v in visits:
            key = (v.patient_id, v.service_code, v.visit_date)
            self.statuses[key].add(v.status)
            if v.status == "verified":
                # Several visits on the same day are added together.
                self.verified_minutes[key] += v.minutes

    def minutes(self, patient_id: str, service: str, day: date) -> Optional[int]:
        """Total verified minutes that day, or None if there's no verified visit."""
        return self.verified_minutes.get((patient_id, service, day))

    def statuses_on(self, patient_id: str, service: str, day: date) -> set[str]:
        return self.statuses.get((patient_id, service, day), set())

    def verified_days_near(self, patient_id: str, service: str, day: date) -> list[date]:
        """Verified visits the day before or after (may mean the date of service is wrong)."""
        nearby = [day - timedelta(days=1), day + timedelta(days=1)]
        return [d for d in nearby if (patient_id, service, d) in self.verified_minutes]


@dataclass
class RuleContext:
    visits: VisitIndex
    auths: dict[str, Authorization]
    payer_rules: dict[str, dict]     # payer_id -> contents of rules/PY0x.yaml

    def valid_auths_for(self, claim: Claim) -> list[str]:
        return [a.auth_id for a in self.auths.values() if a.covers(claim)]


# --- Helpers that read the payer's YAML -------------------------------------------------

def units_supported(minutes: int, rounding: str) -> int:
    """Convert verified minutes to billable 15-minute units."""
    full_units, leftover = divmod(minutes, 15)
    if rounding == "eight_minute_rule":
        return full_units + (1 if leftover >= 8 else 0)
    return full_units   # whole_units: partial units are not billable


def applies_from(entry: dict, day: date) -> bool:
    """True if a YAML entry with an optional `dos_from` applies to this date of service."""
    start = entry.get("dos_from")
    if isinstance(start, str):
        start = date.fromisoformat(start)
    return start is None or day >= start


def filing_limit_days(rules: dict, day: date) -> Optional[int]:
    """Days allowed to file, or None if the payer has no limit. The latest matching entry wins."""
    matching = [e["days"] for e in rules["timely_filing"] if applies_from(e, day)]
    return matching[-1] if matching else None


def required_modifier(rules: dict, claim: Claim) -> Optional[str]:
    """The modifier the payer requires for this claim, or None if it doesn't care."""
    is_weekend = claim.date_of_service.weekday() >= 5
    required = None
    for entry in rules["modifiers"]:
        if entry["service"] != claim.service_code or not applies_from(entry, claim.date_of_service):
            continue
        if entry.get("weekend_only") and not is_weekend:
            continue
        required = entry["modifier"]   # later entries override earlier ones
    return required


# --- The checks --------------------------------------------------------------------------

def check_evv_visit(claim: Claim, ctx: RuleContext, ledger: Ledger) -> Optional[Issue]:
    """D01: there must be a verified EVV visit for this patient, service and date."""
    if ctx.visits.minutes(claim.patient_id, claim.service_code, claim.date_of_service) is not None:
        return None
    return Issue("D01", "No verified EVV visit for the date of service", {
        "unverified_statuses": sorted(ctx.visits.statuses_on(claim.patient_id, claim.service_code,
                                                             claim.date_of_service)),
        "verified_days_near": [d.isoformat() for d in ctx.visits.verified_days_near(
            claim.patient_id, claim.service_code, claim.date_of_service)],
    })


def check_units_vs_evv(claim: Claim, ctx: RuleContext, ledger: Ledger) -> Optional[Issue]:
    """D09: billed units can't exceed what the verified visit time supports."""
    minutes = ctx.visits.minutes(claim.patient_id, claim.service_code, claim.date_of_service)
    if minutes is None:
        return None   # D01 covers this
    supported = units_supported(minutes, ctx.payer_rules[claim.payer_id]["evv_rounding"])
    if claim.units <= supported:
        return None
    return Issue("D09", f"{claim.units} units billed but verified time ({minutes} min) supports {supported}",
                 {"verified_minutes": minutes, "units_supported": supported})


def check_timely_filing(claim: Claim, ctx: RuleContext, ledger: Ledger) -> Optional[Issue]:
    """D03: the claim must be submitted within the payer's filing limit."""
    limit = filing_limit_days(ctx.payer_rules[claim.payer_id], claim.date_of_service)
    days_late = (claim.submitted_date - claim.date_of_service).days
    if limit is None or days_late <= limit:
        return None
    return Issue("D03", f"Submitted {days_late} days after the date of service (limit {limit})",
                 {"days": days_late, "limit": limit})


def check_modifier(claim: Claim, ctx: RuleContext, ledger: Ledger) -> Optional[Issue]:
    """D04: the payer may require a specific modifier for the service and date."""
    required = required_modifier(ctx.payer_rules[claim.payer_id], claim)
    if required is None or claim.modifier == required:
        return None
    return Issue("D04", f"Modifier '{claim.modifier or 'none'}' should be {required}",
                 {"required": required})


def check_duplicate(claim: Claim, ctx: RuleContext, ledger: Ledger) -> Optional[Issue]:
    """D05: a new claim matching an earlier one (any outcome) is a duplicate. Corrections are exempt."""
    if claim.frequency_code != "1":
        return None
    earlier = ledger.earlier_claim(claim)
    if earlier is None:
        return None
    return Issue("D05", f"Duplicate of {earlier.claim_id}",
                 {"original_claim_id": earlier.claim_id, "original_paid": earlier.paid})


def check_auth_missing(claim: Claim, ctx: RuleContext, ledger: Ledger) -> Optional[Issue]:
    """D07: the claim must quote a prior authorization number."""
    if claim.auth_id:
        return None
    return Issue("D07", "Prior authorization number missing",
                 {"valid_auths": ctx.valid_auths_for(claim)})


def check_auth_matches(claim: Claim, ctx: RuleContext, ledger: Ledger) -> Optional[Issue]:
    """D08: the authorization must be for this patient, payer, service and date."""
    auth = ctx.auths.get(claim.auth_id)
    if auth is None or auth.covers(claim):
        return None
    problems = []
    if auth.patient_id != claim.patient_id:
        problems.append("patient")
    if auth.payer_id != claim.payer_id:
        problems.append("payer")
    if auth.service_code != claim.service_code:
        problems.append("service")
    if not auth.start_date <= claim.date_of_service <= auth.end_date:
        problems.append(f"date (covers {auth.start_date} to {auth.end_date})")
    return Issue("D08", f"{claim.auth_id} doesn't match the claim's {', '.join(problems)}",
                 {"valid_auths": ctx.valid_auths_for(claim)})


def check_auth_units(claim: Claim, ctx: RuleContext, ledger: Ledger) -> Optional[Issue]:
    """D02: units can't exceed what's left on the authorization (stateful)."""
    remaining = ledger.remaining_units(claim.auth_id)
    if remaining is None or claim.units <= remaining:
        return None
    return Issue("D02", f"{claim.units} units billed but only {remaining} left on {claim.auth_id}",
                 {"remaining": remaining})


def check_daily_max(claim: Claim, ctx: RuleContext, ledger: Ledger) -> Optional[Issue]:
    """D06: some payers cap a member's paid units per day across all services (stateful)."""
    cap = ctx.payer_rules[claim.payer_id].get("daily_unit_max")
    if cap is None:
        return None
    already_paid = ledger.paid_units_on_day(claim.payer_id, claim.patient_id, claim.date_of_service)
    if already_paid + claim.units <= cap:
        return None
    return Issue("D06", f"{already_paid} units already paid that day + {claim.units} exceeds the {cap}/day cap",
                 {"remaining_today": max(cap - already_paid, 0)})


def check_contract_rules(claim: Claim, ctx: RuleContext, ledger: Ledger) -> list[Issue]:
    """Contract-specific rules from the payer's YAML (e.g. Summit amendment 3)."""
    issues = []
    for rule in ctx.payer_rules[claim.payer_id].get("contract_rules", []):
        if not applies_from(rule, claim.date_of_service):
            continue
        if rule["type"] == "units_multiple_of" and claim.units % rule["value"] != 0:
            issues.append(Issue(rule["code"], rule["description"], {"rule": rule}))
        elif rule["type"] == "max_units_per_claim" and claim.units > rule["value"]:
            issues.append(Issue(rule["code"], rule["description"], {"rule": rule}))
    return issues


SINGLE_CHECKS = [
    check_evv_visit, check_units_vs_evv, check_timely_filing, check_modifier, check_duplicate,
    check_auth_missing, check_auth_matches, check_auth_units, check_daily_max,
]


def check_claim(claim: Claim, ctx: RuleContext, ledger: Ledger) -> list[Issue]:
    """Run every rule and return every issue found (not just the one the payer will report)."""
    issues = [issue for check in SINGLE_CHECKS if (issue := check(claim, ctx, ledger))]
    return issues + check_contract_rules(claim, ctx, ledger)


def reported_issue(issues: list[Issue], precedence: list[str]) -> Optional[Issue]:
    """The one issue the payer will report: the first in the payer's precedence order."""
    if not issues:
        return None
    rank = {code: i for i, code in enumerate(precedence)}
    return min(issues, key=lambda issue: rank.get(issue.code, len(rank)))
