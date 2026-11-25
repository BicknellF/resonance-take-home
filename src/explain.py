"""Turn a checked claim into a short plain-English message for a billing person.

The rules have already decided everything (issues, fixes, outcome). The LLM only
rewords those facts; it can't change the decision. If the LLM is unavailable or
fails, a template message is used instead.
"""

import json
import os

from .config import EXPLAIN_MODEL
from .models import CheckResult

SYSTEM_PROMPT = """You write short notes for billing staff at a US home-care agency.
You are given the facts about one claim, checked before submission: the problems a rules
engine found and the fix for each. Write 2-3 plain sentences: what's wrong, what was or
needs to be done, and why. Use only the facts given. Do not add new problems, fixes,
rules or advice. Do not use jargon beyond denial codes. No greeting or sign-off."""

OUTCOME_TEXT = {
    "pass": "No problems found. Ready to submit.",
    "auto_fixed": "Fixed automatically. No action needed unless you disagree.",
    "do_not_submit": "Don't submit this claim.",
    "needs_person": "Needs a person: at least one problem can't be fixed automatically.",
}


def facts(result: CheckResult) -> dict:
    """Everything the LLM is allowed to talk about."""
    c = result.claim
    return {
        "claim_id": c.claim_id, "payer": c.payer_id, "service": c.service_code,
        "date_of_service": c.date_of_service.isoformat(), "units": c.units,
        "predicted_outcome": f"denied {result.predicted_code}" if result.predicted_code else "paid",
        "problems": [f"{i.code}: {i.message}" for i in result.issues],
        "fixes": [{"fix": f.description, "automatic": f.auto} for f in result.fixes],
        "fixed_units": result.fixed_claim.units if result.fixed_claim else None,
        "outcome": OUTCOME_TEXT[result.outcome],
        "notes": result.notes,
    }


def template_explanation(result: CheckResult) -> str:
    """Deterministic fallback: the same facts, as plain lines."""
    lines = [OUTCOME_TEXT[result.outcome]]
    lines += [f"- Problem {i.code}: {i.message}" for i in result.issues]
    for fix in result.fixes:
        who = "done automatically" if fix.auto else "needs a person"
        lines.append(f"- Fix: {fix.description} ({who})")
    if result.fixed_claim:
        lines.append(f"- Units {result.claim.units} -> {result.fixed_claim.units}; re-checked: passes")
    lines += [f"- {note}" for note in result.notes]
    return "\n".join(lines)


def llm_explanation(result: CheckResult) -> str:
    """Ask Claude to reword the facts. Raises on any API problem; callers fall back."""
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=EXPLAIN_MODEL,
        max_tokens=2000,
        output_config={"effort": "low"},   # short rewording task
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(facts(result), indent=2)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("empty response")
    return text


def explain(result: CheckResult, use_llm: bool = True) -> str:
    if result.outcome == "pass" or not use_llm or not llm_available():
        return template_explanation(result)
    try:
        return llm_explanation(result)
    except Exception as error:   # any LLM failure: never block the billing team
        return template_explanation(result) + f"\n(LLM unavailable: {type(error).__name__})"


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
