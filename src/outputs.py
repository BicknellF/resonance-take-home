"""Write the pipeline's results to output/ as CSV and Markdown."""

import csv
from dataclasses import asdict
from pathlib import Path

from .config import OUTPUT_DIR
from .load import Dataset
from .models import CheckResult


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        if not rows:
            f.write("")
            return
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def to_check_results(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.claim.source == "to_check"]


def write_predictions(results: list[CheckResult], dataset: Dataset) -> Path:
    """predictions.csv: claim_id,status,denial_code for each claim AS SUBMITTED (before fixes)."""
    rows = [{"claim_id": r.claim.claim_id, "status": r.predicted_status, "denial_code": r.predicted_code}
            for r in to_check_results(results)]
    held = sorted({h.claim_id for h in dataset.holds if h.source == "to_check"})
    rows += [{"claim_id": cid, "status": "needs_data_fix", "denial_code": ""} for cid in held]
    path = OUTPUT_DIR / "predictions.csv"
    write_csv(path, rows)
    return path


def write_corrected_claims(results: list[CheckResult]) -> Path:
    """corrected_claims.csv: auto-fixed claims, what changed and why."""
    rows = []
    for r in to_check_results(results):
        if r.outcome != "auto_fixed":
            continue
        before, after = r.claim, r.fixed_claim
        changed = [f"{k}: {getattr(before, k)} -> {getattr(after, k)}"
                   for k in ("units", "modifier", "frequency_code", "original_claim_id", "billed_amount")
                   if getattr(before, k) != getattr(after, k)]
        row = {k: v for k, v in asdict(after).items()
               if k not in ("source", "actual_status", "actual_code", "remark")}
        row.update({"changes": "; ".join(changed), "reasons": "; ".join(f.description for f in r.fixes)})
        rows.append(row)
    path = OUTPUT_DIR / "corrected_claims.csv"
    write_csv(path, rows)
    return path


def write_review_queue(results: list[CheckResult]) -> Path:
    """review_queue.csv: every to-check claim that isn't a clean pass, with all problems and fixes."""
    rows = []
    for r in to_check_results(results):
        if r.outcome == "pass":
            continue
        rows.append({
            "claim_id": r.claim.claim_id, "payer_id": r.claim.payer_id, "outcome": r.outcome,
            "predicted_code": r.predicted_code,
            "issues": " | ".join(f"{i.code}: {i.message}" for i in r.issues),
            "fixes": " | ".join(f"{f.code}: {f.description}{'' if f.auto else ' [person]'}" for f in r.fixes),
            "notes": " | ".join(r.notes),
        })
    path = OUTPUT_DIR / "review_queue.csv"
    write_csv(path, rows)
    return path


def write_holds(dataset: Dataset) -> Path:
    path = OUTPUT_DIR / "data_issues.csv"
    write_csv(path, [asdict(h) for h in dataset.holds])
    return path


def summary(results: list[CheckResult], dataset: Dataset) -> str:
    """A short text summary of the to-check run, including dollars at stake."""
    checked = to_check_results(results)
    held = {h.claim_id for h in dataset.holds if h.source == "to_check"}
    count = lambda outcome: sum(r.outcome == outcome for r in checked)
    recovered = sum(r.fixed_claim.billed_amount for r in checked if r.outcome == "auto_fixed")
    at_risk = sum(r.claim.billed_amount for r in checked if r.outcome == "needs_person")
    lines = [
        f"Claims to check: {len(checked) + len(held)}",
        f"  pass as submitted:        {count('pass')}",
        f"  auto-fixed:               {count('auto_fixed')}  (${recovered:,.2f} now payable)",
        f"  don't submit (duplicate): {count('do_not_submit')}",
        f"  needs a person:           {count('needs_person')}  (${at_risk:,.2f} at risk)",
        f"  on hold (data issue):     {len(held)}",
    ]
    return "\n".join(lines)
