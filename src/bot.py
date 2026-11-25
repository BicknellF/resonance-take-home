"""Discord bot: the human-in-the-loop layer (a stand-in for Slack).

Channels (create them in your server with these exact names):
  #claim-review         auto fixes (FYI, ❌ to undo) and claims that need a person
  #data-issues          claims the cleaner couldn't read (✅ accepts the suggested spelling)
  #rule-proposals       LLM-proposed rules, tested on history (✅ adds to rules YAML)
  #needs-investigation  denials nobody can explain (reply with what you found)

Type `!post` (or `!post 10`) in any channel to post the latest results.
Reply to a bot message to add a note. Every decision goes to output/decisions.jsonl.
"""

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import discord

from . import backtest, engine, explain, propose
from .config import OUTPUT_DIR, add_contract_rule, load_payer_rules
from .load import load_dataset
from .mappings import add_spelling

DECISIONS_FILE = OUTPUT_DIR / "decisions.jsonl"
YES, NO = "✅", "❌"
MAX_MESSAGE = 1900   # Discord's limit is 2000 characters


@dataclass
class Item:
    kind: str     # auto_fix / needs_person / data_issue / proposal / investigation
    data: object  # CheckResult, Hold, or dict


class Pipeline:
    """Runs the checker and keeps the latest results for the bot."""

    def __init__(self, extra_claims_file):
        self.extra = Path(extra_claims_file) if extra_claims_file else None
        self.refresh()

    def refresh(self):
        self.dataset = load_dataset(self.extra)
        self.rules = load_payer_rules()
        self.results = engine.run(self.dataset, self.rules)
        self.proposals = propose.load_json(propose.PROPOSALS_FILE, [])
        self.investigations = propose.load_json(propose.INVESTIGATIONS_FILE, [])

    def to_check(self, outcome):
        return [r for r in self.results if r.claim.source == "to_check" and r.outcome == outcome]

    def accuracy(self) -> str:
        report = backtest.evaluate(self.results)
        return f"{report.accuracy:.1%} ({report.explainable_accuracy:.1%} excluding generic D99s)"


# --- Message formatting ------------------------------------------------------------------

def claim_header(result) -> str:
    c = result.claim
    correction = f" (correction of {c.original_claim_id})" if c.frequency_code == "7" else ""
    return f"{c.claim_id} · {c.payer_id} · {c.service_code} · DOS {c.date_of_service:%d %b}{correction}"


def format_auto_fix(result) -> str:
    title = "NOT SUBMITTED" if result.outcome == "do_not_submit" else "AUTO-FIXED"
    return (f"🔧 **{claim_header(result)}: {title}**\n"
            f"Would have been: DENIED, {result.predicted_code}\n"
            f"{explain.explain(result)}\n"
            f"React {NO} to undo before submission.")


def format_needs_person(result) -> str:
    return (f"⚠️ **{claim_header(result)}: NEEDS A PERSON**\n"
            f"Predicted: DENIED, {result.predicted_code}\n"
            f"{explain.explain(result)}\n"
            f"React {YES} once handled, {NO} if you disagree with the flag, or reply with a note.")


def format_hold(hold) -> str:
    suggestion = f"\nClosest match: **{hold.suggestion}**" if hold.suggestion else ""
    action = f"React {YES} to accept it, " if hold.suggestion else ""
    return (f"🧩 **{hold.claim_id}**: {hold.field} `{hold.value}`: {hold.reason}{suggestion}\n"
            f"{action}{NO} to send it back to the agency, or reply with the right value.")


def format_proposal(p) -> str:
    e, r = p["evidence"], p["rule"]
    return (f"💡 **Proposed rule ({p['payer_id']}): {p['title']}**\n"
            f"{r['description']} (`{r['type']} {r['value']}` from {r.get('dos_from') or 'always'}, code {r['code']})\n"
            f"Why: {p['explanation']}\n"
            f"Tested on history: fixes **{e['newly_correct']}** predictions, wrongly denies "
            f"**{e['new_false_denials']}** paid claims, breaks {e['newly_wrong']} others.\n"
            f"Examples: {', '.join(p['example_claims'])}\n"
            f"React {YES} to add to the rules, {NO} to reject, or reply to ask for changes.")


def format_investigation(inv) -> str:
    return (f"❓ **{inv['claim_id']}** · {inv['payer']} · DOS {inv['date_of_service']} · "
            f"DENIED {inv['actual']} \"{inv['payer_remark']}\"\n"
            f"No rule explains this. LLM's best guess ({inv['group']}): {inv['llm_guess']}\n"
            f"React {YES} once resolved, {NO} if it can't be resolved, or reply with what you found.")


def item_label(item: Item) -> str:
    """A short ID for the decision log: the claim ID, or the proposal title."""
    data = item.data
    if hasattr(data, "claim"):        # CheckResult
        return data.claim.claim_id
    if hasattr(data, "claim_id"):     # Hold
        return data.claim_id
    return data.get("claim_id") or data.get("title", "")


def log_decision(user, item: Item, action: str, detail: str = "") -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    record = {"time": datetime.now().isoformat(timespec="seconds"), "user": str(user),
              "kind": item.kind, "item": item_label(item), "action": action, "detail": detail}
    with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# --- The bot -----------------------------------------------------------------------------

class ReviewBot(discord.Client):
    def __init__(self, pipeline: Pipeline):
        intents = discord.Intents.default()
        intents.message_content = True   # needed to read replies and !post
        super().__init__(intents=intents)
        self.pipeline = pipeline
        self.items: dict[int, Item] = {}   # message id -> what it's about

    async def on_ready(self):
        print(f"Logged in as {self.user}. Type !post in a channel to post results.")

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.content.startswith("!post"):
            parts = message.content.split()
            limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
            await self.post_all(message.guild, limit)
        elif message.reference and message.reference.message_id in self.items:
            item = self.items[message.reference.message_id]
            await self.handle_reply(message, item)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        item = self.items.get(payload.message_id)
        if item is None or payload.user_id == self.user.id or str(payload.emoji) not in (YES, NO):
            return
        channel = self.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        await self.handle_reaction(message, item, str(payload.emoji), payload.member or payload.user_id)

    # Posting

    async def post_all(self, guild: discord.Guild, limit: int):
        p = self.pipeline
        await self.post(guild, "claim-review", "needs_person", p.to_check("needs_person")[:limit], format_needs_person)
        auto = (p.to_check("auto_fixed") + p.to_check("do_not_submit"))[:limit]
        await self.post(guild, "claim-review", "auto_fix", auto, format_auto_fix)
        held = [h for h in p.dataset.holds if h.source == "to_check"][:limit]
        await self.post(guild, "data-issues", "data_issue", held, format_hold)
        await self.post(guild, "rule-proposals", "proposal", p.proposals[:limit], format_proposal)
        await self.post(guild, "needs-investigation", "investigation", p.investigations[:limit], format_investigation)

    async def post(self, guild, channel_name, kind, things, formatter):
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if channel is None or not things:
            return
        for thing in things:
            text = await asyncio.to_thread(formatter, thing)   # may call the LLM
            sent = await channel.send(text[:MAX_MESSAGE])
            self.items[sent.id] = Item(kind, thing)

    # Reactions

    async def handle_reaction(self, message, item: Item, emoji: str, user):
        approve = emoji == YES
        if item.kind == "auto_fix" and not approve:
            log_decision(user, item, "undo")
            await message.reply("Undone. This claim now goes to a person instead of being auto-fixed.")

        elif item.kind == "needs_person":
            log_decision(user, item, "handled" if approve else "disagree")
            await message.reply("Logged as handled." if approve else
                                "Logged as a disagreement. Repeated disagreements flag the rule for review.")

        elif item.kind == "data_issue":
            hold = item.data
            if approve and hold.suggestion:
                value = hold.suggestion.split()[0]   # "PY01 (Northwind Health Plan)" -> "PY01"
                add_spelling(hold.field, hold.value, value)
                log_decision(user, item, "mapping_added", f"{hold.value} -> {value}")
                await asyncio.to_thread(self.pipeline.refresh)
                await message.reply(f"Added `{hold.value}` → `{value}` to mappings.yaml and re-ran. "
                                    f"{self.outcome_text(hold.claim_id)}")
            elif not approve:
                log_decision(user, item, "send_back")
                await message.reply("Sent back to the agency to fix at the source.")

        elif item.kind == "proposal":
            proposal = item.data
            if approve:
                rule = dict(proposal["rule"])
                added = add_contract_rule(proposal["payer_id"], rule)
                log_decision(user, item, "rule_added" if added else "rule_already_exists")
                await asyncio.to_thread(self.pipeline.refresh)
                await message.reply(f"Rule added to rules/{proposal['payer_id']}.yaml. "
                                    f"Back-test now: {self.pipeline.accuracy()}")
            else:
                rejected = propose.load_json(propose.REJECTED_FILE, [])
                rejected.append(proposal["rule"])
                propose.REJECTED_FILE.write_text(json.dumps(rejected, indent=2, default=str), encoding="utf-8")
                log_decision(user, item, "rule_rejected")
                await message.reply("Rejected. It won't be proposed again.")

        elif item.kind == "investigation":
            log_decision(user, item, "resolved" if approve else "unresolvable")
            await message.reply("Logged.")

    # Text replies

    async def handle_reply(self, message, item: Item):
        text = message.content.strip()
        log_decision(message.author, item, "note", text)

        if item.kind == "data_issue":
            hold = item.data
            if hold.field in ("payer", "service_code", "modifier"):
                add_spelling(hold.field, hold.value, text)
                await asyncio.to_thread(self.pipeline.refresh)
                await message.reply(f"Mapped `{hold.value}` → `{text}` and re-ran. {self.outcome_text(hold.claim_id)}")
            else:
                await message.reply("Noted. The claim goes back to the agency with your correction.")

        elif item.kind == "investigation":
            with open(propose.FINDINGS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"claim_id": item.data["claim_id"], "text": text}) + "\n")
            await message.reply("Thanks. Your finding will be used the next time rules are proposed.")

        elif item.kind == "proposal":
            if not explain.llm_available():
                await message.reply("Noted. (No API key set, so the LLM can't revise the proposal.)")
                return
            await message.reply("Asking the LLM to revise this proposal...")
            p = self.pipeline
            new = await asyncio.to_thread(propose.revise, item.data, text, p.dataset, p.rules, p.results)
            if new is None:
                await message.reply("The revised proposal didn't improve the back-test, so it was dropped.")
            else:
                sent = await message.channel.send(format_proposal(new)[:MAX_MESSAGE])
                self.items[sent.id] = Item("proposal", new)
        else:
            await message.reply("Noted.")

    def outcome_text(self, claim_id: str) -> str:
        result = next((r for r in self.pipeline.results if r.claim.claim_id == claim_id), None)
        if result is None:
            return f"{claim_id} is still on hold."
        return f"{claim_id} now: **{result.outcome}** (predicted {result.predicted_status} {result.predicted_code})."


def main(args):
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set the DISCORD_TOKEN environment variable (your Discord bot token).")
    ReviewBot(Pipeline(args.extra)).run(token)
