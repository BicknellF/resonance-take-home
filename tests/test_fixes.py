"""Auto fixes follow the approved list and the guardrails."""


def to_check(results):
    return [r for r in results if r.claim.source == "to_check"]


def test_authorization_problems_always_go_to_a_person(results):
    for r in to_check(results):
        if any(i.code in ("D07", "D08") for i in r.issues):
            assert r.outcome == "needs_person", r.claim.claim_id


def test_contract_and_cap_unit_cuts_always_go_to_a_person(results):
    for r in to_check(results):
        if any(i.code in ("D06", "D10") for i in r.issues) and r.outcome != "do_not_submit":
            assert r.outcome == "needs_person", r.claim.claim_id


SUBMITTER_CODES = {"D03", "D09"}   # visit time and filing limit: the submitter sees and fixes these


def test_visit_time_and_filing_limit_go_back_to_the_submitter(results):
    returned = [r for r in to_check(results) if r.outcome == "return_to_submitter"]
    assert returned
    for r in returned:
        codes = {i.code for i in r.issues}
        assert codes & SUBMITTER_CODES, r.claim.claim_id
        assert all(f.auto or f.submitter for f in r.fixes), r.claim.claim_id
    # and a claim with only submitter (or auto) problems never reaches a reviewer
    for r in to_check(results):
        only_submitter = all(f.auto or f.submitter for f in r.fixes) and any(f.submitter for f in r.fixes)
        if r.issues and r.outcome != "do_not_submit" and only_submitter:
            assert r.outcome == "return_to_submitter", r.claim.claim_id


def test_submitter_problem_plus_reviewer_problem_goes_to_a_reviewer(by_id):
    r = by_id["C020683"]   # D09 (submitter) but also D02 auth used up (reviewer)
    assert {i.code for i in r.issues} >= {"D09", "D02"}
    assert r.outcome == "needs_person"


def test_auto_fixes_never_change_units_or_billing(results):
    fixed = [r for r in to_check(results) if r.outcome == "auto_fixed"]
    assert fixed
    for r in fixed:
        assert r.fixed_claim.units == r.claim.units
        assert r.fixed_claim.billed_amount == r.claim.billed_amount


def test_suggested_cut_accounts_for_issues_the_cut_exposes(by_id):
    # Cutting C019037 to its visit time (19 units) breaks Summit's whole-hours rule, so the
    # suggestion to the submitter is 16. Nothing is changed automatically.
    r = by_id["C019037"]
    assert r.outcome == "return_to_submitter" and r.fixed_claim is None
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
