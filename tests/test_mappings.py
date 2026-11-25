"""Approving a spelling in #data-issues teaches the cleaner (tested on a temp copy)."""

import shutil
from collections import Counter

from src import mappings
from src.config import MAPPINGS_FILE, load_yaml
from src.load import ClaimCleaner
from tests.test_load import GOOD_ROW


def test_approved_spellings_are_used_next_time(dataset, tmp_path, monkeypatch):
    temp = tmp_path / "mappings.yaml"
    shutil.copy(MAPPINGS_FILE, temp)
    monkeypatch.setattr(mappings, "MAPPINGS_FILE", temp)

    mappings.add_spelling("payer", "Northwnd Health", "PY01")
    mappings.add_spelling("service_code", "T1019-A", "T1019")

    cleaner = ClaimCleaner(load_yaml(temp), dataset.auths, dataset.visits, Counter())
    claim, holds = cleaner.clean({**GOOD_ROW, "payer": "Northwnd Health", "service_code": "T1019-A"},
                                 "to_check", {"C1"})
    assert holds == []
    assert claim.payer_id == "PY01" and claim.service_code == "T1019"
