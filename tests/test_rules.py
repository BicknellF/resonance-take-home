"""The rule helpers that read the payer YAML."""

from dataclasses import replace
from datetime import date

from src.rules import filing_limit_days, required_modifier, units_supported


def test_rounding():
    assert units_supported(37, "eight_minute_rule") == 2    # 2 full units + 7 min
    assert units_supported(38, "eight_minute_rule") == 3    # 2 full units + 8 min
    assert units_supported(44, "whole_units") == 2


def test_northwind_filing_limit_changes_on_1_august(rules):
    assert filing_limit_days(rules["PY01"], date(2026, 7, 31)) == 30
    assert filing_limit_days(rules["PY01"], date(2026, 8, 1)) == 45
    assert filing_limit_days(rules["PY02"], date(2026, 8, 1)) is None


def test_harbor_weekend_modifier_from_september(rules, results):
    claim = next(r.claim for r in results if r.claim.payer_id == "PY02" and r.claim.service_code == "T1019")
    saturday_aug, saturday_sep, monday_sep = date(2026, 8, 29), date(2026, 9, 5), date(2026, 9, 7)
    assert required_modifier(rules["PY02"], replace(claim, date_of_service=saturday_aug)) == "U2"
    assert required_modifier(rules["PY02"], replace(claim, date_of_service=saturday_sep)) == "U3"
    assert required_modifier(rules["PY02"], replace(claim, date_of_service=monday_sep)) == "U2"
