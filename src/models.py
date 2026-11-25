"""Plain data objects passed between the pipeline stages."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Optional


@dataclass(frozen=True)
class Claim:
    """One cleaned claim. Fields match the CSV columns, with clean types."""
    claim_id: str
    agency_id: str
    payer_id: str
    patient_id: str
    service_code: str
    date_of_service: date
    submitted_date: date
    units: int
    auth_id: str              # "" when missing
    modifier: str             # "" when none
    frequency_code: str       # "1" new, "7" corrected
    original_claim_id: str    # set for corrected claims
    billed_amount: float
    source: str               # "history" or "to_check"
    # Known outcome (history only)
    actual_status: str = ""   # "paid" / "denied"
    actual_code: str = ""     # e.g. "D03"
    remark: str = ""

    @property
    def rate_per_unit(self) -> float:
        return round(self.billed_amount / self.units, 2)

    @property
    def duplicate_key(self) -> tuple:
        """Payers treat a new claim with the same key as a duplicate."""
        return (self.patient_id, self.service_code, self.date_of_service, self.payer_id)


@dataclass(frozen=True)
class Authorization:
    auth_id: str
    patient_id: str
    payer_id: str
    service_code: str
    start_date: date
    end_date: date
    units_authorized: int

    def covers(self, claim: Claim) -> bool:
        return (self.patient_id == claim.patient_id
                and self.payer_id == claim.payer_id
                and self.service_code == claim.service_code
                and self.start_date <= claim.date_of_service <= self.end_date)


@dataclass(frozen=True)
class Visit:
    """One EVV record. A visit belongs to the date the caregiver clocked in."""
    visit_id: str
    patient_id: str
    service_code: str
    clock_in: datetime
    clock_out: datetime
    status: str               # verified / rejected / manual_edit_pending

    @property
    def visit_date(self) -> date:
        return self.clock_in.date()

    @property
    def minutes(self) -> int:
        return int((self.clock_out - self.clock_in).total_seconds() // 60)


@dataclass
class Hold:
    """A claim the cleaner couldn't read. Sent to a person, never guessed."""
    claim_id: str
    source: str
    field: str
    value: str
    reason: str
    suggestion: str = ""      # closest known value, if any


@dataclass
class Issue:
    """A rule the claim breaks."""
    code: str                 # denial code, e.g. "D09"
    message: str              # short human-readable reason
    details: dict = field(default_factory=dict)


@dataclass
class Fix:
    """What to do about one issue."""
    code: str
    description: str
    auto: bool                                          # True = applied without asking
    apply: Optional[Callable[[Claim], Claim]] = None    # how to change the claim (if auto=False,
                                                        # only used to suggest the change to a person)
    stop_claim: bool = False                            # True = don't submit at all
    submitter: bool = False                             # True = shown to whoever submits the claim
                                                        # (they know the visit / dates); no reviewer


@dataclass
class CheckResult:
    """Everything the engine found out about one claim."""
    claim: Claim
    issues: list[Issue]
    predicted_code: str                     # "" = predicted paid
    outcome: str = "pass"                   # pass / auto_fixed / do_not_submit /
                                            # return_to_submitter / needs_person
    fixes: list[Fix] = field(default_factory=list)
    fixed_claim: Optional[Claim] = None
    suggested_claim: Optional[Claim] = None  # what the suggested (non-auto) fixes would give
    notes: list[str] = field(default_factory=list)

    @property
    def predicted_status(self) -> str:
        return "denied" if self.predicted_code else "paid"
