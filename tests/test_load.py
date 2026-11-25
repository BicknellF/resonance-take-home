"""Runtime cleaning: known problems are fixed, anything unrecognised is held."""

from collections import Counter

from src.config import MAPPINGS_FILE, load_yaml
from src.load import ClaimCleaner

GOOD_ROW = {
    "claim_id": "C1", "agency_id": "AG001", "payer": "NORTHWIND HP", "patient_id": "PT0251",
    "service_code": "T1019 ", "date_of_service": "10/22/2026", "submitted_date": "2026-10-29",
    "units": "9", "auth_id": "AUTH13317", "modifier": " u2", "frequency_code": "1",
    "original_claim_id": "", "billed_amount": "54.9",
}


def clean(dataset, **changes):
    cleaner = ClaimCleaner(load_yaml(MAPPINGS_FILE), dataset.auths, dataset.visits, Counter())
    return cleaner.clean({**GOOD_ROW, **changes}, "to_check", {"C1"})


def test_known_problems_are_fixed(dataset):
    claim, holds = clean(dataset)
    assert holds == []
    assert claim.payer_id == "PY01"
    assert claim.service_code == "T1019"
    assert claim.modifier == "U2"
    assert claim.date_of_service.isoformat() == "2026-10-22"


def test_misspelled_payer_is_held_with_suggestion(dataset):
    claim, holds = clean(dataset, payer="Northwnd Health")
    assert claim is None
    assert holds[0].field == "payer" and holds[0].suggestion.startswith("PY01")


def test_unknown_date_formats_and_impossible_dates_are_held(dataset):
    for bad in ["13-09-2026", "Sept 13", "02/30/2026"]:
        claim, holds = clean(dataset, date_of_service=bad)
        assert claim is None and holds[0].field == "date_of_service"


def test_other_unreadable_values_are_held(dataset):
    assert clean(dataset, service_code="T1019-A")[1][0].field == "service_code"
    assert clean(dataset, units="2.5")[1][0].field == "units"
    assert clean(dataset, auth_id="AUTH99999")[1][0].field == "auth_id"
    assert clean(dataset, payer="")[1][0].reason == "required field is missing"


def test_real_data_has_no_holds_and_duplicates_dropped(dataset):
    assert dataset.holds == []
    assert dataset.cleaning_log["exact duplicate history rows dropped"] == 32
