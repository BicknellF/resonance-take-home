"""A person's decisions go back through the agent: re-cleaned, re-checked, ledger updated."""

import pytest

from src import engine, overrides
from src.load import load_dataset


def run_with(edits, rules):
    dataset = load_dataset(edits=edits)
    return dataset, {r.claim.claim_id: r for r in engine.run(dataset, rules)}


def test_typed_corrections_are_parsed():
    assert overrides.parse("units 16, auth AUTH10782") == {"units": "16", "auth_id": "AUTH10782"}
    assert overrides.parse("dos=2026-09-03; modifier: none") == {"date_of_service": "2026-09-03", "modifier": ""}
    with pytest.raises(ValueError):
        overrides.parse("colour blue")


def test_unit_edit_reprices_at_the_same_rate():
    row = {"units": "20", "billed_amount": "96.00"}
    assert overrides.apply_to_row(row, {"units": "16"})["billed_amount"] == "76.80"


def test_edits_are_saved_and_merged(tmp_path, monkeypatch):
    monkeypatch.setattr(overrides, "EDITS_FILE", tmp_path / "edits.json")
    overrides.record("C1", {"units": "16"})
    overrides.record("C1", {"auth_id": "AUTH1"}, no_auto_fix=True)
    assert overrides.load() == {"C1": {"units": "16", "auth_id": "AUTH1", overrides.NO_AUTO_FIX: True}}
    assert overrides.clear("C1") and overrides.load() == {}


def test_accepted_suggestion_is_rechecked(by_id, rules):
    before = by_id["C019037"]
    assert before.outcome == "return_to_submitter"
    _, after = run_with({"C019037": overrides.changes_between(before.claim, before.suggested_claim)}, rules)
    assert after["C019037"].outcome == "pass"
    assert any("Edited by a person" in n for n in after["C019037"].notes)


def test_a_persons_bad_value_is_caught_not_trusted(rules):
    # 17 units fits the visit time but breaks Summit's whole-hours rule.
    _, after = run_with({"C019037": {"units": "17"}}, rules)
    assert after["C019037"].predicted_code == "D10"
    # A mistyped authorization is held by the cleaner, like any other unreadable value.
    dataset, _ = run_with({"C020934": {"auth_id": "AUTH99999"}}, rules)
    assert "C020934" in dataset.held_claim_ids


def test_edit_updates_the_ledger_for_later_claims(by_id, rules):
    # Fixing C019627's authorization makes it paid, which uses up AUTH13920 for C019010.
    assert by_id["C019010"].outcome == "pass"
    _, after = run_with({"C019627": {"auth_id": "AUTH13920"}}, rules)
    assert after["C019627"].outcome == "pass"
    assert after["C019010"].predicted_code == "D02"


def test_undoing_an_auto_fix_sends_the_claim_to_a_person(results, rules):
    auto = next(r for r in results if r.outcome == "auto_fixed")
    _, after = run_with({auto.claim.claim_id: {overrides.NO_AUTO_FIX: True}}, rules)
    assert after[auto.claim.claim_id].outcome == "needs_person"
