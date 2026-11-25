"""A person's decisions on to-check claims, sent back through the agent.

Every human action (accept a suggested fix, type a correction, undo an auto fix, fix
an unreadable value) is stored here as an edit and the whole pipeline runs again.
Edits are applied to the raw CSV row at load time, *before* cleaning, so a person's
value is cleaned and checked exactly like the agency's (a mistyped authorization is
held, not trusted). The edited claim then takes its place in the one timeline, so the
ledger, and every later claim on the same authorization or day, is re-checked too.

Stored in output/claim_edits.json. The source data files are never modified.
"""

import json
from typing import Optional

from .config import OUTPUT_DIR
from .models import Claim

EDITS_FILE = OUTPUT_DIR / "claim_edits.json"
NO_AUTO_FIX = "no_auto_fix"   # the person undid an auto fix: send this claim to a person instead

EDITABLE = {"payer", "patient_id", "service_code", "date_of_service", "submitted_date", "units",
            "auth_id", "modifier", "frequency_code", "original_claim_id", "billed_amount"}
ALIASES = {"dos": "date_of_service", "date": "date_of_service", "auth": "auth_id", "unit": "units",
           "mod": "modifier", "frequency": "frequency_code", "original": "original_claim_id",
           "billed": "billed_amount", "amount": "billed_amount", "service": "service_code",
           "patient": "patient_id"}
BLANK = {"none", "blank", "-", '""'}
# Fields a suggested correction can change (see fixes.suggest_correction).
SUGGESTABLE = ("units", "modifier", "auth_id", "frequency_code", "original_claim_id")


def load() -> dict[str, dict]:
    if not EDITS_FILE.exists():
        return {}
    return json.loads(EDITS_FILE.read_text(encoding="utf-8"))


def save(edits: dict[str, dict]) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    EDITS_FILE.write_text(json.dumps(edits, indent=2, sort_keys=True), encoding="utf-8")


def record(claim_id: str, changes: dict[str, str], no_auto_fix: bool = False) -> dict:
    """Add a person's changes to a claim (merged with earlier ones). Returns the claim's edits."""
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ValueError(f"can't edit {', '.join(sorted(unknown))}")
    edits = load()
    entry = edits.setdefault(claim_id, {})
    entry.update({k: str(v) for k, v in changes.items()})
    if no_auto_fix:
        entry[NO_AUTO_FIX] = True
    save(edits)
    return entry


def clear(claim_id: str) -> bool:
    edits = load()
    if edits.pop(claim_id, None) is None:
        return False
    save(edits)
    return True


def parse(text: str) -> dict[str, str]:
    """Read a typed correction: "units 16", "auth AUTH10782, dos 2026-09-03", "modifier=none".

    Raises ValueError with a helpful message if any part isn't `field value`.
    """
    changes = {}
    for part in text.replace(";", ",").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        for sep in ("=", ":", " "):
            if sep in part:
                key, value = part.split(sep, 1)
                break
        else:
            raise ValueError(f"'{part}' needs a field and a value, e.g. 'units 16'")
        key = key.strip().lower().replace(" ", "_")
        key = ALIASES.get(key, key)
        if key not in EDITABLE:
            raise ValueError(f"unknown field '{key}'. Editable: {', '.join(sorted(EDITABLE))}")
        value = value.strip()
        changes[key] = "" if value.lower() in BLANK else value
    if not changes:
        raise ValueError("nothing to change. Example: 'units 16' or 'auth AUTH10782'")
    return changes


def apply_to_row(row: dict, edit: dict) -> dict:
    """The raw row with a person's edits applied. A unit change re-prices at the same rate."""
    new = dict(row)
    for key, value in edit.items():
        if key in EDITABLE:
            new[key] = value
    if "units" in edit and "billed_amount" not in edit:
        try:
            rate = float(row["billed_amount"]) / int(str(row["units"]).strip())
            new["billed_amount"] = f"{rate * int(edit['units']):.2f}"
        except (KeyError, ValueError, ZeroDivisionError):
            pass   # leave it: the cleaner will hold the claim if the amount is unreadable
    return new


def changes_between(old: Claim, new: Optional[Claim]) -> dict[str, str]:
    """The raw-field edits that turn `old` into `new` (used when a person accepts a suggestion)."""
    if new is None:
        return {}
    return {name: str(getattr(new, name)) for name in SUGGESTABLE if getattr(old, name) != getattr(new, name)}


def describe(edit: dict) -> str:
    parts = [f"{k} = {v or 'blank'}" for k, v in sorted(edit.items()) if k in EDITABLE]
    if edit.get(NO_AUTO_FIX):
        parts.append("auto fixes turned off")
    return ", ".join(parts)
