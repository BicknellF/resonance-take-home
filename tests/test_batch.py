"""The batch as sent: auto fixes are booked in the ledger, and corrections must prove a fix.

Small hand-built datasets, so each case is exact.
"""

from datetime import date, datetime

from src import engine
from src.load import Dataset
from src.models import Authorization, Claim, Visit

SAT = date(2026, 9, 5)   # a Saturday after Harbor's weekend bulletin: T1019 needs U3
RATE = {"T1019": 6.10, "S5125": 5.40}


def claim(cid, service, units, submitted, modifier="", source="to_check", freq="1", original="",
          status="", code=""):
    return Claim(claim_id=cid, agency_id="AG001", payer_id="PY02", patient_id="PT9001", service_code=service,
                 date_of_service=SAT, submitted_date=submitted, units=units, auth_id=f"AUTH-{service}",
                 modifier=modifier, frequency_code=freq, original_claim_id=original,
                 billed_amount=round(units * RATE[service], 2), source=source,
                 actual_status=status, actual_code=code)


def dataset(claims):
    auths = {f"AUTH-{s}": Authorization(f"AUTH-{s}", "PT9001", "PY02", s, date(2026, 9, 1), date(2026, 11, 30), 500)
             for s in RATE}
    visits = [Visit(f"V-{s}", "PT9001", s, datetime(2026, 9, 5, 8), datetime(2026, 9, 5, 15), "verified")
              for s in RATE]   # 7 hours each = 28 whole units
    return Dataset(claims=claims, holds=[], auths=auths, visits=visits)


def results(claims, rules):
    return {r.claim.claim_id: r for r in engine.run(dataset(claims), rules)}


def test_auto_fixed_claim_counts_towards_harbor_daily_cap(rules):
    # 24 units with the wrong weekend modifier: auto-fixed to U3, so it will be paid.
    # A later 16 units for the same member and day then takes them to 40 > 32.
    out = results([claim("X1", "T1019", 24, date(2026, 9, 6), modifier="U2"),
                   claim("X2", "S5125", 16, date(2026, 9, 7))], rules)
    assert out["X1"].outcome == "auto_fixed" and out["X1"].fixed_claim.modifier == "U3"
    assert out["X2"].predicted_code == "D06"
    assert out["X2"].outcome == "needs_person"
    assert any("re-checked with the batch's auto fixes" in n for n in out["X2"].notes)


def test_without_the_fix_the_second_claim_fits_the_cap(rules):
    out = results([claim("X1", "T1019", 24, date(2026, 9, 6), modifier="U3"),   # already right
                   claim("X2", "S5125", 8, date(2026, 9, 7))], rules)
    assert out["X1"].outcome == "pass" and out["X2"].outcome == "pass"


def test_correction_of_a_paid_claim_goes_to_a_person(rules):
    out = results([claim("H1", "T1019", 8, date(2026, 9, 6), modifier="U3", source="history", status="paid"),
                   claim("X3", "T1019", 8, date(2026, 9, 8), modifier="U3", freq="7", original="H1")], rules)
    assert out["X3"].issues == [] and out["X3"].outcome == "needs_person"
    assert any("was paid" in n for n in out["X3"].notes)


def test_correction_of_an_unexplained_denial_goes_to_a_person(rules):
    out = results([claim("H1", "T1019", 8, date(2026, 9, 6), modifier="U3", source="history",
                         status="denied", code="D99"),
                   claim("X3", "T1019", 8, date(2026, 9, 8), modifier="U3", freq="7", original="H1")], rules)
    assert out["X3"].outcome == "needs_person"
    assert any("no stated reason" in n for n in out["X3"].notes)


def test_correction_of_a_different_visit_goes_to_a_person(rules):
    out = results([claim("H1", "S5125", 8, date(2026, 9, 6), source="history", status="denied", code="D07"),
                   claim("X3", "T1019", 8, date(2026, 9, 8), modifier="U3", freq="7", original="H1")], rules)
    assert out["X3"].outcome == "needs_person"
    assert any("different patient, service" in n for n in out["X3"].notes)


def test_correction_that_fixes_a_known_denial_passes(rules):
    out = results([claim("H1", "T1019", 8, date(2026, 9, 6), modifier="U2", source="history",
                         status="denied", code="D04"),
                   claim("X3", "T1019", 8, date(2026, 9, 8), modifier="U3", freq="7", original="H1")], rules)
    assert out["X3"].outcome == "pass"
    assert any("original problem D04 is fixed" in n for n in out["X3"].notes)


def test_duplicate_of_an_unexplained_denial_is_not_auto_converted(by_id):
    # Both originals were denied D99 (generic remark): nothing shows a resubmission fixes them.
    for cid in ("C021652", "C021640"):
        r = by_id[cid]
        assert r.outcome == "needs_person" and r.fixed_claim is None, cid
        assert r.suggested_claim.frequency_code == "7", cid
