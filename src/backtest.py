"""Compare predictions against what actually happened to the historical claims."""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from .models import CheckResult

# Rules were tuned on all of the history, so splitting by period is a weak holdout.
# Shown anyway so drift over time would be visible.
PERIOD_SPLIT = date(2026, 9, 1)


@dataclass
class BacktestReport:
    total: int
    correct: int
    unexplainable: int          # generic D99s no rule could predict
    correct_explainable: int
    by_period: dict[str, tuple[int, int]]   # period -> (correct, total)
    confusion: pd.DataFrame
    misses: list[CheckResult]
    hidden_outcomes: list[str]

    @property
    def accuracy(self) -> float:
        return self.correct / self.total

    @property
    def explainable_accuracy(self) -> float:
        return self.correct_explainable / (self.total - self.unexplainable)


def is_unexplainable(result: CheckResult) -> bool:
    """A generic D99 ('contact payer') that no rule flagged."""
    return result.claim.actual_code == "D99" and not result.issues


def evaluate(results: list[CheckResult]) -> BacktestReport:
    history = [r for r in results if r.claim.source == "history"]
    correct = [r for r in history if r.predicted_code == r.claim.actual_code]
    misses = [r for r in history if r.predicted_code != r.claim.actual_code]
    unexplainable = [r for r in history if is_unexplainable(r)]

    by_period = {}
    for name, keep in [(f"submitted before {PERIOD_SPLIT}", lambda d: d < PERIOD_SPLIT),
                       (f"submitted from {PERIOD_SPLIT}", lambda d: d >= PERIOD_SPLIT)]:
        subset = [r for r in history if keep(r.claim.submitted_date)]
        by_period[name] = (sum(r.predicted_code == r.claim.actual_code for r in subset), len(subset))

    confusion = pd.crosstab(
        pd.Series([r.claim.actual_code or "PAID" for r in history], name="actual"),
        pd.Series([r.predicted_code or "PAID" for r in history], name="predicted"),
    )

    return BacktestReport(
        total=len(history), correct=len(correct), unexplainable=len(unexplainable),
        correct_explainable=len(correct),   # unexplainable ones are never correct
        by_period=by_period, confusion=confusion, misses=misses,
        hidden_outcomes=find_hidden_outcomes(results),
    )


def find_hidden_outcomes(results: list[CheckResult]) -> list[str]:
    """To-check claims that history says were probably denied, though every rule passes.

    If a history claim was actually PAID but we predict D02 (not enough units), an earlier
    to-check claim on the same authorization that we predicted paid probably wasn't.
    """
    suspects = []
    for i, r in enumerate(results):
        c = r.claim
        if c.source == "history" and c.actual_status == "paid" and r.predicted_code == "D02":
            for earlier in results[:i]:
                e = earlier.claim
                if e.source == "to_check" and e.auth_id == c.auth_id and not earlier.predicted_code:
                    suspects.append(
                        f"{e.claim_id}: history paid {c.claim_id} on {c.auth_id}, which only adds up "
                        f"if {e.claim_id} was denied (all rules pass, so the reason is unknown)")
    return suspects


def format_report(report: BacktestReport) -> str:
    lines = [
        "# Back-test against claims history",
        "",
        f"- Claims: **{report.total}**",
        f"- Exact match (status and denial code): **{report.accuracy:.1%}** ({report.correct}/{report.total})",
        f"- Excluding {report.unexplainable} generic D99 denials no rule can predict: "
        f"**{report.explainable_accuracy:.1%}**",
        "",
        "Rules were tuned on the whole history, so the period split below is a weak holdout:",
        "",
    ]
    for name, (correct, total) in report.by_period.items():
        lines.append(f"- {name}: {correct / total:.1%} ({correct}/{total})")
    lines += ["", "## Confusion matrix (rows = actual, columns = predicted)", "",
              "```", report.confusion.to_string(), "```",
              "", "## Misses (excluding generic D99s)", ""]
    for r in report.misses:
        if not is_unexplainable(r):
            c = r.claim
            lines.append(f"- {c.claim_id} ({c.payer_id}): actual {c.actual_code or 'PAID'}, "
                         f"predicted {r.predicted_code or 'PAID'}. Remark: {c.remark or '-'}")
    lines += ["", "## Hidden outcomes (to-check claims history suggests were denied)", ""]
    lines += [f"- {s}" for s in report.hidden_outcomes] or ["- none"]
    return "\n".join(lines) + "\n"

