"""The running state the engine carries from claim to claim.

Claims are processed in submission order. After each claim is decided, `record()`
updates three things:
  - units left on each authorization            (for D02)
  - paid units per member per day, per payer    (for Harbor's daily cap, D06)
  - every claim seen so far                     (for duplicates, D05)

Only paid claims draw down units. Every claim (paid or denied) counts for duplicates.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .models import Authorization, Claim


@dataclass
class SeenClaim:
    claim_id: str
    paid: bool


class Ledger:
    def __init__(self, auths: dict[str, Authorization]):
        self.units_left: dict[str, int] = {a.auth_id: a.units_authorized for a in auths.values()}
        self.paid_per_day: dict[tuple, int] = defaultdict(int)
        self.first_claim: dict[tuple, SeenClaim] = {}

    def remaining_units(self, auth_id: str) -> Optional[int]:
        return self.units_left.get(auth_id)

    def paid_units_on_day(self, payer_id: str, patient_id: str, day: date) -> int:
        return self.paid_per_day[(payer_id, patient_id, day)]

    def earlier_claim(self, claim: Claim) -> Optional[SeenClaim]:
        """The first claim already submitted with the same patient, service, date and payer."""
        return self.first_claim.get(claim.duplicate_key)

    def record(self, claim: Claim, paid: bool) -> None:
        self.first_claim.setdefault(claim.duplicate_key, SeenClaim(claim.claim_id, paid))
        if paid:
            if claim.auth_id in self.units_left:
                self.units_left[claim.auth_id] -= claim.units
            self.paid_per_day[(claim.payer_id, claim.patient_id, claim.date_of_service)] += claim.units
