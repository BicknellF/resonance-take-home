"""Rule proposals are tested on history before anyone sees them (LLM answer faked here)."""

from src import engine, propose
from src.config import load_payer_rules
from src.propose import Group, Grouping, ProposedRule


def test_rediscovered_amendment_is_tested_and_generic_d99s_go_to_people(dataset):
    rules = load_payer_rules(without_contract_rules=True)
    results = engine.run(dataset, rules)
    misses = propose.collect_misses(results)
    summit_ids = [m["claim_id"] for m in misses if "amendment 3" in m["payer_remark"]]
    generic_ids = [m["claim_id"] for m in misses if "contact payer" in m["payer_remark"]
                   or "Under review" in m["payer_remark"]]

    fake_llm_answer = Grouping(groups=[
        Group(title="Summit whole hours", claim_ids=summit_ids + ["C999999"],   # one invented ID
              explanation="Remarks cite amendment 3", is_rule=True,
              rule=ProposedRule(payer_id="PY03", type="units_multiple_of", value=4,
                                dos_from="2026-07-01", code="D10", description="Bill whole hours")),
        Group(title="Generic payer denials", claim_ids=generic_ids,
              explanation="No pattern", is_rule=False, rule=None),
    ])
    proposals, investigations = propose.build_outputs(fake_llm_answer, misses, dataset, rules, results)

    assert len(proposals) == 1
    assert proposals[0]["evidence"]["newly_correct"] >= 35
    assert proposals[0]["evidence"]["new_false_denials"] == 0
    assert "C999999" not in proposals[0]["example_claims"]
    assert {i["claim_id"] for i in investigations} == set(generic_ids)
