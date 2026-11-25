"""Load and clean the data at runtime. The source files are never modified.

Known problems are fixed and counted in `cleaning_log`. Anything not recognised puts
the claim on hold (a `Hold`) for a person to look at; it is never guessed.
"""

import difflib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import DATA_DIR, MAPPINGS_FILE, load_yaml
from .models import Authorization, Claim, Hold, Visit
from .overrides import apply_to_row


@dataclass
class Dataset:
    claims: list[Claim]                     # clean claims, history + to-check
    holds: list[Hold]                       # problems found (one claim can have several)
    auths: dict[str, Authorization]
    visits: list[Visit]
    cleaning_log: Counter = field(default_factory=Counter)
    edits: dict = field(default_factory=dict)  # a person's edits to to-check claims (see overrides.py)

    @property
    def held_claim_ids(self) -> set[str]:
        return {h.claim_id for h in self.holds}

    def claim(self, claim_id: str) -> Optional[Claim]:
        return next((c for c in self.claims if c.claim_id == claim_id), None)


def read_csv(path: Path) -> pd.DataFrame:
    """Read everything as text, so nothing is silently converted."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_dataset(extra_claims_file: Optional[Path] = None, edits: Optional[dict] = None) -> Dataset:
    """Load every file. `extra_claims_file` adds more to-check claims (used for the demo).

    `edits` (claim_id -> field changes, from overrides.py) are a person's corrections to
    to-check claims. They're applied to the raw row before cleaning, so they're checked
    like any other value.
    """
    edits = edits or {}
    mappings = load_yaml(MAPPINGS_FILE)
    auths = load_authorizations()
    visits = load_visits()
    log = Counter()

    history = read_csv(DATA_DIR / "claims_history.csv")
    before = len(history)
    history = history.drop_duplicates()
    log["exact duplicate history rows dropped"] = before - len(history)

    to_check = read_csv(DATA_DIR / "claims_to_check.csv")
    if extra_claims_file:
        to_check = pd.concat([to_check, read_csv(extra_claims_file)], ignore_index=True)

    cleaner = ClaimCleaner(mappings, auths, visits, log)
    all_ids = set(history.claim_id) | set(to_check.claim_id)
    claims, holds = [], []
    for source, frame in [("history", history), ("to_check", to_check)]:
        for row in frame.to_dict("records"):
            edit = edits.get(str(row.get("claim_id", "")).strip()) if source == "to_check" else None
            if edit:
                row = apply_to_row(row, edit)
                log["to-check claims edited by a person"] += 1
            claim, row_holds = cleaner.clean(row, source, all_ids)
            holds.extend(row_holds)
            if claim:
                claims.append(claim)

    return Dataset(claims=claims, holds=holds, auths=auths, visits=visits, cleaning_log=log, edits=edits)


def load_authorizations() -> dict[str, Authorization]:
    df = read_csv(DATA_DIR / "authorizations.csv")
    return {
        r["auth_id"]: Authorization(
            auth_id=r["auth_id"], patient_id=r["patient_id"], payer_id=r["payer_id"],
            service_code=r["service_code"].strip(),
            start_date=date.fromisoformat(r["start_date"]),
            end_date=date.fromisoformat(r["end_date"]),
            units_authorized=int(r["units_authorized"]),
        )
        for r in df.to_dict("records")
    }


def load_visits() -> list[Visit]:
    df = read_csv(DATA_DIR / "evv_visits.csv")
    fmt = "%Y-%m-%d %H:%M"
    return [
        Visit(
            visit_id=r["visit_id"], patient_id=r["patient_id"],
            service_code=r["service_code"].strip(),
            clock_in=datetime.strptime(r["clock_in"], fmt),
            clock_out=datetime.strptime(r["clock_out"], fmt),
            status=r["verification_status"].strip(),
        )
        for r in df.to_dict("records")
    ]


class ClaimCleaner:
    """Turns one raw CSV row into a clean `Claim`, or holds it."""

    def __init__(self, mappings: dict, auths: dict, visits: list[Visit], log: Counter):
        self.log = log
        self.auths = auths
        self.date_formats = mappings["date_formats"]
        self.service_codes = set(mappings["service_codes"])
        self.modifiers = set(mappings["modifiers"])
        self.aliases = mappings.get("aliases") or {}   # e.g. {"service_code": {"T1019-A": "T1019"}}
        # "northwind hp" -> "PY01", etc.
        self.payer_lookup = {
            normalise_name(spelling): payer_id
            for payer_id, spellings in mappings["payers"].items()
            for spelling in spellings
        }
        self.payer_names = {payer_id: spellings[-1] for payer_id, spellings in mappings["payers"].items()}
        self.known_patients = {a.patient_id for a in auths.values()} | {v.patient_id for v in visits}

    def clean(self, row: dict, source: str, all_claim_ids: set[str]) -> tuple[Optional[Claim], list[Hold]]:
        holds: list[Hold] = []

        def hold(field_name: str, reason: str, suggestion: str = "") -> None:
            holds.append(Hold(row.get("claim_id", ""), source, field_name,
                              str(row.get(field_name, "")), reason, suggestion))

        # Required fields
        for name in ["claim_id", "payer", "patient_id", "service_code", "date_of_service",
                     "submitted_date", "units", "frequency_code"]:
            if not str(row.get(name, "")).strip():
                hold(name, "required field is missing")
        if holds:
            return None, holds

        payer_id = self.clean_payer(row["payer"], hold)
        service_code = self.clean_code(row["service_code"], self.service_codes, "service_code", hold)
        modifier = self.clean_code(row.get("modifier", ""), self.modifiers, "modifier", hold)
        dos = self.parse_date(row["date_of_service"], "date_of_service", hold)
        submitted = self.parse_date(row["submitted_date"], "submitted_date", hold)
        units = self.parse_units(row["units"], hold)

        frequency = row["frequency_code"].strip()
        if frequency not in {"1", "7"}:
            hold("frequency_code", "frequency code must be 1 (new) or 7 (corrected)")
        original_id = row.get("original_claim_id", "").strip()
        if frequency == "7" and original_id not in all_claim_ids:
            hold("original_claim_id", "corrected claim points to an unknown original claim")

        auth_id = row.get("auth_id", "").strip()
        if auth_id and auth_id not in self.auths:
            hold("auth_id", "authorization not found in authorizations.csv")
        if row["patient_id"].strip() not in self.known_patients:
            hold("patient_id", "patient has no authorizations or visits on file")
        if dos and submitted and submitted < dos:
            hold("submitted_date", "submitted before the date of service")

        try:
            billed = float(row.get("billed_amount", ""))
        except ValueError:
            hold("billed_amount", "billed amount is not a number")
            billed = 0.0

        if holds:
            return None, holds

        return Claim(
            claim_id=row["claim_id"].strip(), agency_id=row.get("agency_id", "").strip(),
            payer_id=payer_id, patient_id=row["patient_id"].strip(), service_code=service_code,
            date_of_service=dos, submitted_date=submitted, units=units, auth_id=auth_id,
            modifier=modifier, frequency_code=frequency, original_claim_id=original_id,
            billed_amount=billed, source=source,
            actual_status=row.get("status", "").strip(), actual_code=row.get("denial_code", "").strip(),
            remark=row.get("remark", "").strip(),
        ), []

    # Field cleaners. Each one either returns a clean value or records a hold.

    def clean_payer(self, raw: str, hold) -> str:
        payer_id = self.payer_lookup.get(normalise_name(raw))
        if payer_id is None:
            hold("payer", "payer name not recognised", self.suggest_payer(raw))
            return ""
        if raw != payer_id:
            self.log[f"payer name mapped: '{raw}' -> {payer_id}"] += 1
        return payer_id

    def clean_code(self, raw: str, known: set[str], field_name: str, hold) -> str:
        cleaned = raw.strip().upper()
        if cleaned in self.aliases.get(field_name, {}):        # approved by a person earlier
            cleaned = self.aliases[field_name][cleaned]
            self.log[f"{field_name} alias applied: '{raw}' -> {cleaned}"] += 1
            return cleaned
        if cleaned not in known:
            hold(field_name, f"{field_name} not recognised", closest(cleaned, known - {""}))
            return ""
        if cleaned != raw:
            self.log[f"{field_name} whitespace/case fixed"] += 1
        return cleaned

    def parse_date(self, raw: str, field_name: str, hold) -> Optional[date]:
        raw = raw.strip()
        for i, fmt in enumerate(self.date_formats):
            try:
                parsed = datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
            if i > 0:
                self.log[f"{field_name} converted from {fmt}"] += 1
            return parsed
        hold(field_name, "date format not recognised, or not a real date")
        return None

    def parse_units(self, raw: str, hold) -> int:
        raw = raw.strip()
        if not raw.isdigit() or int(raw) == 0:
            hold("units", "units must be a positive whole number")
            return 0
        return int(raw)

    def suggest_payer(self, raw: str) -> str:
        match = closest(normalise_name(raw), set(self.payer_lookup))
        if not match:
            return ""
        payer_id = self.payer_lookup[match]
        return f"{payer_id} ({self.payer_names[payer_id]})"


def normalise_name(name: str) -> str:
    return " ".join(name.lower().split())


def closest(value: str, options: set[str]) -> str:
    matches = difflib.get_close_matches(value, sorted(options), n=1, cutoff=0.6)
    return matches[0] if matches else ""
