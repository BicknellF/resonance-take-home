"""Back-test the engine against the claims history."""

from src import backtest


def test_backtest_accuracy(results):
    report = backtest.evaluate(results)
    assert report.accuracy >= 0.98
    assert report.explainable_accuracy >= 0.995


def test_every_denial_code_except_d99_is_predicted(results):
    report = backtest.evaluate(results)
    unexpected = [r.claim.claim_id for r in report.misses
                  if r.claim.actual_code != "D99" and r.claim.actual_status != "paid"]
    assert unexpected == []


def test_ledger_includes_to_check_claims(by_id):
    # AUTH10782 has 32 units. To-check C020563 (16 units) is paid first, so history
    # C010286 (24 units) is denied D02, although history alone shows 32 units left.
    assert by_id["C020563"].predicted_code == ""
    assert by_id["C010286"].predicted_code == "D02"


def test_payer_specific_precedence(by_id):
    # Summit reports a missing auth (D07) ahead of units over visit time (D09)
    assert by_id["C003125"].predicted_code == "D07"
    # Harbor reports D09 ahead of D07
    assert by_id["C000393"].predicted_code == "D09"


def test_hidden_outcome_is_flagged(results):
    assert any(s.startswith("C021301") for s in backtest.find_hidden_outcomes(results))
