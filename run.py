"""Command-line entry point.

    python run.py check                    # check claims, back-test, write output/
    python run.py check --extra demo/odd_claims.csv     # include badly formatted demo claims
    python run.py propose --without-contract-rules      # LLM groups unexplained denials
    python run.py explain C021766          # plain-English explanation for one claim
    python run.py bot                      # Discord bot (needs DISCORD_TOKEN)
    python run.py edit C019037 units=16    # a person's correction, sent back through the agent
    python run.py edit C019037 --clear     # remove a person's edits
    python run.py ablate                   # accuracy with parts of the agent switched on/off
"""

import argparse
from pathlib import Path

from src import backtest, engine, outputs, overrides
from src.config import OUTPUT_DIR, load_env, load_payer_rules
from src.load import load_dataset


def run_pipeline(args):
    """Load (with every person's saved edits), check, and return (dataset, rules, results)."""
    return run_pipeline_with(args, overrides.load())


def run_pipeline_with(args, edits):
    dataset = load_dataset(Path(args.extra) if args.extra else None, edits)
    rules = load_payer_rules(without_contract_rules=args.without_contract_rules)
    return dataset, rules, engine.run(dataset, rules)


def cmd_check(args):
    dataset, rules, results = run_pipeline(args)

    print("Cleaning (fixed automatically):")
    for what, n in sorted(dataset.cleaning_log.items()):
        print(f"  {n:5d}  {what}")
    print(f"Held for a person (unreadable data): {len(dataset.held_claim_ids)} claims\n")

    report = backtest.evaluate(results)
    (OUTPUT_DIR / "backtest_report.md").parent.mkdir(exist_ok=True)
    (OUTPUT_DIR / "backtest_report.md").write_text(backtest.format_report(report), encoding="utf-8")
    print(f"Back-test: {report.accuracy:.1%} exact match on {report.total} past claims "
          f"({report.explainable_accuracy:.1%} excluding {report.unexplainable} generic D99s)\n")

    print(outputs.summary(results, dataset))
    for path in [outputs.write_predictions(results, dataset), outputs.write_corrected_claims(results),
                 outputs.write_review_queue(results), outputs.write_holds(dataset),
                 OUTPUT_DIR / "backtest_report.md"]:
        print(f"wrote {path.relative_to(OUTPUT_DIR.parent)}")


def cmd_propose(args):
    from src import explain, propose
    if not explain.llm_available():
        raise SystemExit("Set ANTHROPIC_API_KEY first: proposing rules needs the LLM.")
    dataset, rules, results = run_pipeline(args)
    proposals, investigations = propose.run(dataset, rules, results)
    print(f"{len(proposals)} rule proposals, {len(investigations)} claims for investigation")
    for p in proposals:
        print(f"  - {p['title']}: catches {p['evidence']['newly_correct']} denials, "
              f"wrongly denies {p['evidence']['new_false_denials']} paid claims")


def cmd_explain(args):
    from src import explain
    dataset, rules, results = run_pipeline(args)
    result = next((r for r in results if r.claim.claim_id == args.claim_id), None)
    if result is None:
        print(f"{args.claim_id} not found (or on hold for a data issue)")
        return
    print(explain.explain(result, use_llm=not args.no_llm))


def cmd_edit(args):
    """The same loop as the Discord bot, from the command line: save the edit, re-run, report."""
    if args.clear:
        print(f"Cleared edits on {args.claim_id}" if overrides.clear(args.claim_id) else "No edits to clear")
    else:
        changes = overrides.parse(", ".join(args.changes)) if args.changes else {}
        if not changes and not args.no_auto_fix:
            raise SystemExit("Give changes like units=16 auth=AUTH10782, or --no-auto-fix, or --clear")
        overrides.record(args.claim_id, changes, no_auto_fix=args.no_auto_fix)
        print(f"Saved: {overrides.describe(overrides.load()[args.claim_id])}")

    _, _, before = run_pipeline_with(args, {k: v for k, v in overrides.load().items() if k != args.claim_id})
    dataset, _, results = run_pipeline(args)
    old = {r.claim.claim_id: r for r in before if r.claim.source == "to_check"}
    new = {r.claim.claim_id: r for r in results if r.claim.source == "to_check"}
    held = [h for h in dataset.holds if h.claim_id == args.claim_id]
    if held:
        print(f"{args.claim_id} is on hold: " + "; ".join(f"{h.field} {h.value!r} ({h.reason})" for h in held))
    elif args.claim_id in new:
        r = new[args.claim_id]
        print(f"{args.claim_id}: {r.outcome} {r.predicted_code}".rstrip())
        for line in [f"  problem {i.code}: {i.message}" for i in r.issues] + [f"  {n}" for n in r.notes]:
            print(line)
    knock_on = [(cid, old[cid], r) for cid, r in new.items() if cid != args.claim_id and cid in old
                and (old[cid].outcome, old[cid].predicted_code) != (r.outcome, r.predicted_code)]
    for cid, o, r in knock_on:
        print(f"  ledger knock-on: {cid} {o.outcome} {o.predicted_code}".rstrip() + f" -> {r.outcome} {r.predicted_code}")


def cmd_ablate(args):
    from src import ablation
    text = ablation.run()
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "ablation.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    print("\nwrote output/ablation.md")


def cmd_bot(args):
    from src import bot
    bot.main(args)


def main():
    load_env()   # reads ANTHROPIC_API_KEY / DISCORD_TOKEN from .env, if present
    parser = argparse.ArgumentParser(description="Pre-submission claim checker")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in [("check", cmd_check), ("propose", cmd_propose), ("explain", cmd_explain), ("bot", cmd_bot),
                       ("edit", cmd_edit), ("ablate", cmd_ablate)]:
        p = sub.add_parser(name)
        p.set_defaults(func=func)
        p.add_argument("--extra", help="extra to-check claims CSV (e.g. demo/odd_claims.csv)")
        p.add_argument("--without-contract-rules", action="store_true",
                       help="drop contract rules (demo: let the LLM rediscover them)")
        p.add_argument("--no-llm", action="store_true", help="use template text instead of the LLM")
        if name in ("explain", "edit"):
            p.add_argument("claim_id")
        if name == "edit":
            p.add_argument("changes", nargs="*", help="field=value pairs, e.g. units=16 auth=AUTH10782")
            p.add_argument("--no-auto-fix", action="store_true", help="undo auto fixes on this claim")
            p.add_argument("--clear", action="store_true", help="remove this claim's edits")
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
