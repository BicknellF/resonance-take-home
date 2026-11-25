"""Ablation study: how accurate the back-test is as parts of the agent are switched on.

Two tables:
  - Cumulative: start from "everything is paid" and add one part at a time.
  - Leave one out: the full agent with one part switched off.

Every variant is scored on the same history claims as the main back-test.
"""

import copy
from dataclasses import dataclass, replace

from . import engine
from .backtest import is_unexplainable
from .config import load_payer_rules
from .load import load_dataset

ALL_CODES = [f"D{n:02d}" for n in range(1, 11)]
STATEFUL = {"D02", "D05", "D06"}             # need the running ledger
CONTRACT = {"D10"}                           # Summit amendment 3, only in payer remarks


@dataclass
class Variant:
    name: str
    note: str
    disabled: set = frozenset()
    bulletins: bool = True                   # PY01 45-day limit, Harbor weekend U3
    contract: bool = True                    # Summit amendment 3 (D10)
    learned_precedence: bool = True          # payer order learned from history vs D01, D02, ... order
    eight_minute_rule: bool = True           # PY01 rounding from the manual (else whole units)
    to_check_in_timeline: bool = True        # replay to-check claims through the ledger too
    drop_duplicate_rows: bool = True


def rules_for(v: Variant, base: dict) -> dict:
    rules = copy.deepcopy(base)
    for r in rules.values():
        if not v.bulletins:
            r["timely_filing"] = [e for e in r["timely_filing"] if "dos_from" not in e]
            r["modifiers"] = [e for e in r["modifiers"] if "dos_from" not in e]
        if not v.contract:
            r["contract_rules"] = []
        if not v.learned_precedence:
            r["precedence"] = ALL_CODES
        if not v.eight_minute_rule and r["evv_rounding"] == "eight_minute_rule":
            r["evv_rounding"] = "whole_units"
    return rules


def score(v: Variant, base_rules: dict, datasets: dict, generic_d99: set) -> dict:
    data = datasets[v.drop_duplicate_rows]
    if not v.to_check_in_timeline:
        data = replace(data, claims=[c for c in data.claims if c.source == "history"])
    history = [r for r in engine.run(data, rules_for(v, base_rules), v.disabled) if r.claim.source == "history"]
    exact = sum(r.predicted_code == r.claim.actual_code for r in history)
    explainable = [r for r in history if r.claim.claim_id not in generic_d99]
    denied = [r for r in history if r.claim.actual_code]
    paid = [r for r in history if not r.claim.actual_code]
    return {
        "claims": len(history),
        "exact": exact / len(history),
        "explainable": sum(r.predicted_code == r.claim.actual_code for r in explainable) / len(explainable),
        "denials_caught": sum(bool(r.predicted_code) for r in denied) / len(denied),
        "false_denials": sum(bool(r.predicted_code) for r in paid),
        "paid_total": len(paid),
    }


CUMULATIVE = [
    Variant("Predict every claim paid", "baseline, no rules", disabled=set(ALL_CODES)),
    Variant("+ Payer manual rules, stateless", "D01 EVV, D03 filing, D04 modifier, D07/D08 auth, D09 units vs minutes",
            disabled=STATEFUL | CONTRACT, bulletins=False, contract=False, learned_precedence=False),
    Variant("+ Payer bulletins", "Northwind 45-day limit from Aug, Harbor weekend U3 from Sep",
            disabled=STATEFUL | CONTRACT, contract=False, learned_precedence=False),
    Variant("+ Summit amendment 3 (from denial remarks)", "D10: whole hours from July",
            disabled=STATEFUL, learned_precedence=False),
    Variant("+ Stateful ledger, history claims only", "D02 auth units, D05 duplicates, D06 Harbor daily cap",
            learned_precedence=False, to_check_in_timeline=False),
    Variant("+ One timeline with the to-check claims", "to-check claims draw on the same auths",
            learned_precedence=False),
    Variant("+ Payer precedence learned from history", "= the full agent"),
]

LEAVE_ONE_OUT = [
    Variant("Full agent", ""),
    Variant("Without dropping the 32 duplicate history rows", "", drop_duplicate_rows=False),
    Variant("Without Northwind 8-minute rule (whole units instead)", "", eight_minute_rule=False),
    Variant("Without payer bulletins", "", bulletins=False),
    Variant("Without Summit amendment 3", "", contract=False),
    Variant("Without to-check claims in the timeline", "", to_check_in_timeline=False),
    Variant("Without stateful ledger (D02, D05, D06)", "", disabled=STATEFUL),
    Variant("Without learned precedence (D01, D02, ... order)", "", learned_precedence=False),
]


def run() -> str:
    base_rules = load_payer_rules()
    datasets = {True: load_dataset(), False: load_dataset(drop_duplicate_rows=False)}
    full = engine.run(datasets[True], base_rules)
    generic_d99 = {r.claim.claim_id for r in full if r.claim.source == "history" and is_unexplainable(r)}

    def table(variants, delta_from_previous: bool) -> list[str]:
        lines = ["| Agent | Exact match | Change | Excl. generic D99s | Denials caught | Paid claims wrongly denied |",
                 "|---|---:|---:|---:|---:|---:|"]
        ref = None   # previous row (cumulative) or the first row (leave one out)
        for v in variants:
            s = score(v, base_rules, datasets, generic_d99)
            change = "" if ref is None else f"{(s['exact'] - ref) * 100:+.1f} pts"
            name = f"**{v.name}**" + (f"<br><sub>{v.note}</sub>" if v.note else "")
            rows = f" ({s['claims']:,} rows)" if s["claims"] != 3830 else ""
            lines.append(f"| {name} | {s['exact']:.1%}{rows} | {change} | {s['explainable']:.1%} | "
                         f"{s['denials_caught']:.1%} | {s['false_denials']} / {s['paid_total']:,} |")
            if delta_from_previous or ref is None:
                ref = s["exact"]
        return lines

    out = ["### Cumulative: adding one part at a time", ""] + table(CUMULATIVE, True)
    out += ["", "### Leave one out: the full agent with one part switched off", ""] + table(LEAVE_ONE_OUT, False)
    return "\n".join(out)
