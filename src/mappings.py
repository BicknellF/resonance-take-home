"""Add spellings approved by a person (from #data-issues) to mappings.yaml."""

from .config import MAPPINGS_FILE, load_yaml, save_yaml


def add_spelling(field: str, raw_value: str, correct_value: str) -> None:
    """Teach the cleaner that `raw_value` means `correct_value` for this field.

    payer:                adds the spelling to that payer's list (correct_value is e.g. "PY01")
    service_code/modifier: adds an alias, e.g. "T1019-A" -> "T1019"
    """
    mappings = load_yaml(MAPPINGS_FILE)
    correct_value = correct_value.strip().upper() if field != "payer" else correct_value.strip()

    if field == "payer":
        payer_id = correct_value.split()[0].upper()
        if payer_id not in mappings["payers"]:
            raise ValueError(f"Unknown payer ID {payer_id}")
        if raw_value not in mappings["payers"][payer_id]:
            mappings["payers"][payer_id].append(raw_value)
    elif field in ("service_code", "modifier"):
        aliases = mappings.setdefault("aliases", {}).setdefault(field, {})
        aliases[raw_value.strip().upper()] = correct_value
    else:
        raise ValueError(f"No mapping for field {field}")

    save_yaml(MAPPINGS_FILE, mappings)
