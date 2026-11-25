"""Group denials the rules didn't predict, and propose new rules (LLM).

1. Collect history claims where our prediction was wrong.
2. Ask Claude (Sonnet) to group them by likely cause and, where a group looks like a
   rule, express it as a contract rule the engine can run (a fixed set of rule types).
3. Test every proposed rule on the full history: how many misses it fixes, and how
   many paid claims it would wrongly deny.
4. Groups that aren't a rule (e.g. generic "contact payer" denials) go to people
   in #needs-investigation, with the LLM's best guess attached.

Nothing goes live until a person approves it in Discord (see bot.py).
"""

import copy
import json
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import engine
from .config import OUTPUT_DIR, PROPOSE_MODEL
from .load import Dataset
from .models import CheckResult

PROPOSALS_FILE = OUTPUT_DIR / "proposals.json"
INVESTIGATIONS_FILE = OUTPUT_DIR / "investigations.json"
REJECTED_FILE = OUTPUT_DIR / "rejected_rules.json"
FINDINGS_FILE = OUTPUT_DIR / "findings.jsonl"   # people's replies from #needs-investigation

SYSTEM_PROMPT = """You help maintain a deterministic claims-checking rules engine for US home-care billing.
You are shown claims where the engine's prediction was wrong, with the payer's own remark.
Group them by likely cause. For each group decide whether it is a consistent, checkable rule.

Only call something a rule if it can be expressed with one of these rule types:
- units_multiple_of: units must be a multiple of `value` (from date of service `dos_from`)
- max_units_per_claim: units may not exceed `value` (from date of service `dos_from`)
Use the denial code the payer actually used most often for that group.
If a group has no consistent, checkable pattern (for example generic remarks such as
"contact payer"), set is_rule to false and give your best guess of the cause in plain words.
Never invent claim IDs: only use IDs from the input."""


class ProposedRule(BaseModel):
    payer_id: str = Field(description="PY01, PY02 or PY03")
    type: Literal["units_multiple_of", "max_units_per_claim"]
    value: int
    dos_from: Optional[str] = Field(description="YYYY-MM-DD, or null if it always applied")
    code: str = Field(description="Denial code, e.g. D10")
    description: str = Field(description="One sentence a billing person would understand")


class Group(BaseModel):
    title: str
    claim_ids: list[str]
    explanation: str
    is_rule: bool
    rule: Optional[ProposedRule]


class Grouping(BaseModel):
    groups: list[Group]


# --- Step 1: collect what we got wrong -------------------------------------------------

def collect_misses(results: list[CheckResult]) -> list[dict]:
    rows = []
    for r in results:
        c = r.claim
        if c.source != "history" or r.predicted_code == c.actual_code:
            continue
        rows.append({
            "claim_id": c.claim_id, "payer": c.payer_id, "agency": c.agency_id,
            "service": c.service_code, "date_of_service": c.date_of_service.isoformat(),
            "weekday": c.date_of_service.strftime("%a"),
            "days_to_submit": (c.submitted_date - c.date_of_service).days,
            "units": c.units, "modifier": c.modifier or "-",
            "actual": c.actual_code or "PAID", "predicted": r.predicted_code or "PAID",
            "payer_remark": c.remark or "-",
        })
    return rows


def load_findings() -> list[str]:
    if not FINDINGS_FILE.exists():
        return []
    return [json.loads(line)["text"] for line in FINDINGS_FILE.read_text(encoding="utf-8").splitlines() if line]


# --- Step 2: ask the LLM ----------------------------------------------------------------

def ask_llm(misses: list[dict], findings: list[str], extra_instruction: str = "") -> Grouping:
    import anthropic

    header = list(misses[0])
    table = "\n".join([",".join(header)] + [",".join(str(m[h]) for h in header) for m in misses])
    content = f"Claims the engine got wrong ({len(misses)}):\n{table}\n"
    if findings:
        content += "\nNotes from billing staff who investigated similar denials:\n" + "\n".join(f"- {f}" for f in findings)
    if extra_instruction:
        content += f"\n{extra_instruction}\n"

    client = anthropic.Anthropic()
    response = client.beta.messages.parse(
        model=PROPOSE_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=Grouping,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",      # if Sonnet declines, the API retries on a fallback model
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"No usable grouping from the model (stop_reason={response.stop_reason})")
    return response.parsed_output


# --- Step 3: test a proposed rule on history -------------------------------------------

def to_yaml_rule(rule: ProposedRule, title: str) -> dict:
    return {
        "id": "proposed-" + "-".join(title.lower().split())[:40],
        "type": rule.type, "value": rule.value, "dos_from": rule.dos_from,
        "code": rule.code, "description": rule.description,
    }


def test_rule(payer_id: str, yaml_rule: dict, dataset: Dataset, rules: dict,
              baseline: list[CheckResult]) -> dict:
    """Re-run the whole history with the rule added and compare with the baseline."""
    trial_rules = copy.deepcopy(rules)
    trial_rules[payer_id]["contract_rules"].append(yaml_rule)
    if yaml_rule["code"] not in trial_rules[payer_id]["precedence"]:
        trial_rules[payer_id]["precedence"].append(yaml_rule["code"])
    trial = engine.run(dataset, trial_rules)

    before = {r.claim.claim_id: r for r in baseline if r.claim.source == "history"}
    newly_correct = new_false_denials = newly_wrong = 0
    for r in trial:
        c = r.claim
        if c.source != "history":
            continue
        was_right = before[c.claim_id].predicted_code == c.actual_code
        is_right = r.predicted_code == c.actual_code
        newly_correct += (not was_right) and is_right
        newly_wrong += was_right and not is_right
        new_false_denials += c.actual_status == "paid" and was_right and bool(r.predicted_code)
    return {"newly_correct": newly_correct, "newly_wrong": newly_wrong, "new_false_denials": new_false_denials}


# --- Orchestration ----------------------------------------------------------------------

def run(dataset: Dataset, rules: dict, results: list[CheckResult]) -> tuple[list[dict], list[dict]]:
    misses = collect_misses(results)
    if not misses:
        return [], []
    grouping = ask_llm(misses, load_findings())
    proposals, investigations = build_outputs(grouping, misses, dataset, rules, results)
    save(proposals, investigations)
    return proposals, investigations


def build_outputs(grouping: Grouping, misses: list[dict], dataset: Dataset, rules: dict,
                  results: list[CheckResult]) -> tuple[list[dict], list[dict]]:
    by_id = {m["claim_id"]: m for m in misses}
    rejected = load_json(REJECTED_FILE, [])
    proposals, investigations = [], []

    for group in grouping.groups:
        ids = [cid for cid in group.claim_ids if cid in by_id]   # drop any invented IDs
        rule = group.rule if group.is_rule else None
        if rule and rule.payer_id in rules:
            yaml_rule = to_yaml_rule(rule, group.title)
            if any(same_rule(yaml_rule, r) for r in rejected):
                continue
            evidence = test_rule(rule.payer_id, yaml_rule, dataset, rules, results)
            if evidence["newly_correct"] > 0:
                proposals.append({"title": group.title, "explanation": group.explanation,
                                  "payer_id": rule.payer_id, "rule": yaml_rule,
                                  "evidence": evidence, "example_claims": ids[:5]})
                continue
        # Not a rule, or the rule didn't help: people investigate each claim.
        for cid in ids:
            investigations.append({"claim_id": cid, "group": group.title, "llm_guess": group.explanation,
                                   **{k: by_id[cid][k] for k in ("payer", "actual", "payer_remark", "date_of_service")}})
    return proposals, investigations


def revise(proposal: dict, feedback: str, dataset: Dataset, rules: dict,
           results: list[CheckResult]) -> Optional[dict]:
    """Re-ask the LLM about one proposal, with a reviewer's feedback. Returns the new proposal."""
    misses = [m for m in collect_misses(results) if m["payer"] == proposal["payer_id"]]
    instruction = (f"A reviewer commented on your earlier proposal '{proposal['title']}' "
                   f"(rule: {json.dumps(proposal['rule'], default=str)}). Their feedback: \"{feedback}\". "
                   f"Return a revised grouping that takes this into account.")
    grouping = ask_llm(misses, load_findings(), instruction)
    new_proposals, _ = build_outputs(grouping, misses, dataset, rules, results)
    return new_proposals[0] if new_proposals else None


def same_rule(a: dict, b: dict) -> bool:
    return (a["type"], a["value"], str(a.get("dos_from"))) == (b["type"], b["value"], str(b.get("dos_from")))


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(proposals: list[dict], investigations: list[dict]) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    PROPOSALS_FILE.write_text(json.dumps(proposals, indent=2, default=str), encoding="utf-8")
    INVESTIGATIONS_FILE.write_text(json.dumps(investigations, indent=2, default=str), encoding="utf-8")
