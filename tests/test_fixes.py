"""Auto fixes follow the approved list and the guardrails."""


def to_check(results):
    return [r for r in results if r.claim.source == "to_check"]


def test_authorization_problems_always_go_to_a_person(results):
    for r in to_check(results):
        if any(i.code in ("D07", "D08") for i in r.issues):
            assert r.outcome == "needs_person", r.claim.claim_id


def test_unit_cuts_always_go_to_a_person(results):
    for r in to_check(results):
        if any(i.code in ("D06", "D09", "D10") for i in r.issues) and r.outcome != "do_not_submit":
            assert r.outcome == "needs_person", r.claim.claim_id


def test_auto_fixes_never_change_units_or_billing(results):
    fixed = [r for r in to_check(results) if r.outcome == "auto_fixed"]
    assert fixed
    for r in fixed:
        assert r.fixed_claim.units == r.claim.units
        assert r.fixed_claim.billed_amount == r.claim.billed_amount


def test_suggested_cut_accounts_for_issues_the_cut_exposes(by_id):
    # Cutting C019037 to its visit time (19 units) breaks Summit's whole-hours rule, so the
    # suggestion to the person is 16. Nothing is changed automatically.
    r = by_id["C019037"]
    assert r.outcome == "needs_person" and r.fixed_claim is None
    assert any("units 20 -> 16" in n and "would then pass" in n for n in r.notes)


def test_duplicate_of_paid_claim_is_not_submitted(results):
    for r in to_check(results):
        d05 = next((i for i in r.issues if i.code == "D05"), None)
        if d05 and d05.details["original_paid"]:
            assert r.outcome == "do_not_submit"


def test_correction_still_failing_is_reported(by_id):
    r = by_id["C021797"]   # fixed its missing auth, but the auth is now out of units
    assert r.outcome == "needs_person" and r.predicted_code == "D02"
    assert any("original problem D07 is fixed" in n for n in r.notes)
