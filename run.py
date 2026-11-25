"""Command-line entry point.

    python run.py check                    # check claims, back-test, write output/
    python run.py check --extra demo/odd_claims.csv     # include badly formatted demo claims
    python run.py propose --without-contract-rules      # LLM groups unexplained denials
    python run.py explain C021766          # plain-English explanation for one claim
    python run.py bot                      # Discord bot (needs DISCORD_TOKEN)
"""

import argparse
from pathlib import Path

from src import backtest, engine, outputs
from src.config import OUTPUT_DIR, load_env, load_payer_rules
from src.load import load_dataset


def run_pipeline(args):
    dataset = load_dataset(Path(args.extra) if args.extra else None)
    rules = load_payer_rules(without_contract_rules=args.without_contract_rules)
    results = engine.run(dataset, rules)
    return dataset, rules, results


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


def cmd_bot(args):
    from src import bot
    bot.main(args)


def main():
    load_env()   # reads ANTHROPIC_API_KEY / DISCORD_TOKEN from .env, if present
    parser = argparse.ArgumentParser(description="Pre-submission claim checker")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in [("check", cmd_check), ("propose", cmd_propose), ("explain", cmd_explain), ("bot", cmd_bot)]:
        p = sub.add_parser(name)
        p.set_defaults(func=func)
        p.add_argument("--extra", help="extra to-check claims CSV (e.g. demo/odd_claims.csv)")
        p.add_argument("--without-contract-rules", action="store_true",
                       help="drop contract rules (demo: let the LLM rediscover them)")
        p.add_argument("--no-llm", action="store_true", help="use template text instead of the LLM")
        if name == "explain":
            p.add_argument("claim_id")
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
