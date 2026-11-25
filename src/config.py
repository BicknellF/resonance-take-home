"""File locations and YAML helpers."""

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "Resonance - graduate preparation data"   # read-only source data
RULES_DIR = ROOT / "rules"
MAPPINGS_FILE = ROOT / "mappings.yaml"
OUTPUT_DIR = ROOT / "output"

PAYER_IDS = ["PY01", "PY02", "PY03"]

# LLM models (see PLAN.md: Haiku for cheap per-claim wording, Sonnet for grouping)
EXPLAIN_MODEL = "claude-haiku-5-5"
PROPOSE_MODEL = "claude-sonnet-5-5"


def load_env(path: Path = ROOT / ".env") -> None:
    """Load KEY=value lines from .env into the environment.

    Blank values are skipped, and variables already set in the shell win over the file.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_yaml(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def load_payer_rules(without_contract_rules: bool = False) -> dict[str, dict]:
    """Load rules/PY0x.yaml for every payer.

    without_contract_rules=True drops contract-specific rules. Used to demo the LLM
    rediscovering Summit's amendment 3 from denial remarks.
    """
    rules = {}
    for payer_id in PAYER_IDS:
        payer_rules = load_yaml(RULES_DIR / f"{payer_id}.yaml")
        if without_contract_rules:
            payer_rules["contract_rules"] = []
        rules[payer_id] = payer_rules
    return rules


def add_contract_rule(payer_id: str, rule: dict) -> bool:
    """Append an approved rule to a payer's YAML. Returns False if an identical rule exists."""
    path = RULES_DIR / f"{payer_id}.yaml"
    payer_rules = load_yaml(path)
    existing = payer_rules.setdefault("contract_rules", [])
    if any(r.get("type") == rule["type"] and r.get("value") == rule["value"]
           and str(r.get("dos_from")) == str(rule.get("dos_from")) for r in existing):
        return False
    existing.append(rule)
    if rule["code"] not in payer_rules["precedence"]:
        payer_rules["precedence"].append(rule["code"])
    save_yaml(path, payer_rules)
    return True
