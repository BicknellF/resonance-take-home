"""Discord bot: the human-in-the-loop layer (a stand-in for Slack).

Channels (create them in your server with these exact names):
  #claim-review         auto fixes (FYI, ❌ to undo) and claims that need a reviewer
  #data-issues          claims the cleaner couldn't read (✅ accepts the suggested spelling)
  #rule-proposals       LLM-proposed rules, tested on history (✅ adds to rules YAML). Only rules
                        with 0 wrongly denied paid claims are posted. The ✅ is a DEMO of approval,
                        not a real control: anyone in the channel can click it.
  #needs-investigation  denials nobody can explain (reply with what you found)

Claims flagged back to the submitter (D09 visit time, D03 filing limit) are not posted: they're
shown to whoever submits the claim, not to a reviewer.

Type `!post` (or `!post 10`) in any channel to post the latest results.
Each item is one short message with its reactions already added, so a reviewer just clicks:
✅ accept / handled, ❌ disagree / undo, 🔍 full explanation in a thread (the LLM is only
called on 🔍). Reply to a claim card with corrections (`units 16, auth AUTH10782`).
Every decision goes to output/decisions.jsonl.

Every action that changes a claim is sent back through the agent (overrides.py): the
edit is saved, the whole timeline is re-checked (so the ledger and later claims update),
and the bot replies with the new outcome. If the claim still fails, a fresh card is posted.
"""

import asyncio
import importlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import discord

from . import backtest, explain, overrides, pipeline, propose
from .config import OUTPUT_DIR, add_contract_rule
from .mappings import add_spelling

DECISIONS_FILE = OUTPUT_DIR / "decisions.jsonl"
YES, NO, MORE = "✅", "❌", "🔍"
SHORT = 110          # max characters for the one-line fix
MAX_MESSAGE = 1900   # Discord's limit is 2000 characters
OUTCOME_ICONS = {"pass": "✅", "auto_fixed": "🔧", "do_not_submit": "🚫", "return_to_submitter": "↩️",
                 "needs_person": "⚠️"}


@dataclass
class Item:
    kind: str     # auto_fix / needs_person / data_issue / proposal / investigation
    data: object  # CheckResult, Hold, or dict


class Pipeline:
    """Runs the checker and keeps the latest run (plus the LLM's proposals) for the bot."""

    def __init__(self, extra_claims_file):
        self.extra = Path(extra_claims_file) if extra_claims_file else None
        self.run = None
        self.refresh()

    def refresh(self) -> dict[str, tuple]:
        """Re-run the whole agent (with every person's edits) and return the to-check claims
        whose outcome changed: {claim_id: (before, after)}, each "outcome code" or "held"."""
        before, self.run = self.run, pipeline.run(self.extra, overrides.load())
        self.proposals = propose.load_json(propose.PROPOSALS_FILE, [])
        self.investigations = propose.load_json(propose.INVESTIGATIONS_FILE, [])
        return pipeline.changed(before, self.run) if before else {}

    @property
    def dataset(self):
        return self.run.dataset

    @property
    def rules(self):
        return self.run.rules

    @property
    def results(self):
        return self.run.results

    def accuracy(self) -> str:
        report = backtest.evaluate(self.results)
        return f"{report.accuracy:.1%} ({report.explainable_accuracy:.1%} excluding generic D99s)"


# --- Message formatting ------------------------------------------------------------------
# Each item has a short card (posted) and a detail text (posted in a thread on 🔍).

def clip(text: str, n: int = SHORT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def claim_header(result) -> str:
    c = result.claim
    correction = f" · corrects {c.original_claim_id}" if c.frequency_code == "7" else ""
    return f"**{c.claim_id}** · {c.payer_id} {c.service_code} · {c.date_of_service:%d %b}{correction}"


def short_fix(result) -> str:
    """One line: the fix, from the rules engine (no LLM)."""
    if not result.fixes:
        return clip(result.issues[0].message) if result.issues else ""
    first = result.fixes[0].description
    if result.fixed_claim and result.fixed_claim.units != result.claim.units:
        first = f"Units {result.claim.units} → {result.fixed_claim.units}. {first}"
    more = f" (+{len(result.fixes) - 1} more)" if len(result.fixes) > 1 else ""
    return clip(first, SHORT - len(more)) + more


def card_needs_person(result) -> str:
    return f"⚠️ {claim_header(result)} · {result.predicted_code}\n{short_fix(result)}"


def card_auto_fix(result) -> str:
    icon, label = ("🚫", "not sent") if result.outcome == "do_not_submit" else ("🔧", "auto-fixed")
    return f"{icon} {claim_header(result)} · {label} {result.predicted_code}\n{short_fix(result)}"


def card_hold(hold) -> str:
    guess = f" → **{hold.suggestion}**?" if hold.suggestion else f": {clip(hold.reason, 60)}"
    return f"🧩 **{hold.claim_id}** · {hold.field} `{hold.value}`{guess}"


def card_proposal(p) -> str:
    e, title = p["evidence"], p["title"]
    if not title.startswith(p["payer_id"]):
        title = f"{p['payer_id']}: {title}"
    return (f"💡 **{clip(title, 80)}**\n"
            f"Back-test: +{e['newly_correct']} right, {e['new_false_denials']} paid claims wrongly denied")


def card_investigation(inv) -> str:
    return (f"❓ **{inv['claim_id']}** · {inv['payer']} · {inv['date_of_service']} · {inv['actual']} "
            f"\"{clip(inv['payer_remark'], 50)}\"\nGuess: {clip(inv['llm_guess'], 90)}")


def detail_claim(result) -> str:
    """The full explanation (LLM, with template fallback) plus every problem and fix."""
    lines = [explain.explain(result), ""]
    lines += [f"• Problem {i.code}: {i.message}" for i in result.issues]
    lines += [f"• Fix: {f.description} ({'automatic' if f.auto else 'needs a person'})" for f in result.fixes]
    return "\n".join(lines)


def detail_hold(hold) -> str:
    return (f"{hold.reason}\n{YES} accepts the suggested value, {NO} sends it back to the agency, "
            f"or reply to the card with the right value.")


def detail_proposal(p) -> str:
    e, r = p["evidence"], p["rule"]
    return (f"{r['description']} (`{r['type']} {r['value']}` from {r.get('dos_from') or 'always'}, "
            f"code {r['code']})\nWhy: {p['explanation']}\n"
            f"Back-test: fixes {e['newly_correct']}, wrongly denies {e['new_false_denials']} paid, "
            f"breaks {e['newly_wrong']} others.\nExamples: {', '.join(p['example_claims'])}\n"
            f"Reply to the card to ask for changes.")


def detail_investigation(inv) -> str:
    return (f"Payer remark: \"{inv['payer_remark']}\"\nLLM's best guess ({inv['group']}): {inv['llm_guess']}\n"
            f"Reply to the card with what you found.")


# kind -> (card, detail, reactions added to the card)
KINDS = {
    "needs_person": (card_needs_person, detail_claim, (YES, NO, MORE)),
    "auto_fix": (card_auto_fix, detail_claim, (NO, MORE)),
    "data_issue": (card_hold, detail_hold, (NO, MORE)),   # plus ✅ when there's a suggestion
    "proposal": (card_proposal, detail_proposal, (YES, NO, MORE)),
    "investigation": (card_investigation, detail_investigation, (YES, NO, MORE)),
}


def reactions_for(kind: str, thing) -> tuple:
    if kind == "data_issue" and thing.suggestion:
        return (YES, NO, MORE)
    return KINDS[kind][2]


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
        self.expanded: set[int] = set()    # cards whose 🔍 detail is already posted

    async def on_ready(self):
        print(f"Logged in as {self.user}. Type !post in a channel to post results.")
        if explain.llm_available():
            # The first `import anthropic` can take ~20s on Windows: do it now, not on the first 🔍.
            await asyncio.to_thread(importlib.import_module, "anthropic")

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
        emoji = str(payload.emoji)
        if item is None or payload.user_id == self.user.id or emoji not in (YES, NO, MORE):
            return
        channel = self.get_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        if emoji == MORE:
            await self.show_detail(message, item)
        else:
            await self.handle_reaction(message, item, emoji, payload.member or payload.user_id)

    # Posting

    async def post_all(self, guild: discord.Guild, limit: int):
        p = self.pipeline
        needs = p.run.to_check("needs_person")
        auto = p.run.to_check("auto_fixed") + p.run.to_check("do_not_submit")
        review = discord.utils.get(guild.text_channels, name="claim-review")
        if review:
            await review.send(f"Posting {min(limit, len(needs))} of {len(needs)} claims needing a person and "
                              f"{min(limit, len(auto))} of {len(auto)} auto-handled. "
                              f"{YES} handled · {NO} disagree/undo · {MORE} details")
        await self.post(guild, "claim-review", "needs_person", needs[:limit])
        await self.post(guild, "claim-review", "auto_fix", auto[:limit])
        held = p.dataset.to_check_holds[:limit]
        await self.post(guild, "data-issues", "data_issue", held)
        await self.post(guild, "rule-proposals", "proposal", p.proposals[:limit])
        await self.post(guild, "needs-investigation", "investigation", p.investigations[:limit])

    async def post(self, guild, channel_name, kind, things):
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if channel is None or not things:
            return
        for thing in things:
            await self.post_one(channel, kind, thing)

    async def post_one(self, channel, kind, thing):
        sent = await channel.send(KINDS[kind][0](thing)[:MAX_MESSAGE])   # no LLM call, so posting is instant
        self.items[sent.id] = Item(kind, thing)
        for emoji in reactions_for(kind, thing):
            await sent.add_reaction(emoji)

    async def show_detail(self, message, item: Item):
        """🔍: post the full explanation in a thread on the card (or a reply if threads aren't allowed)."""
        if message.id in self.expanded:
            return
        self.expanded.add(message.id)
        text = (await asyncio.to_thread(KINDS[item.kind][1], item.data))[:MAX_MESSAGE]   # may call the LLM
        try:
            thread = await message.create_thread(name=f"Details: {item_label(item)}"[:90])
            await thread.send(text)
        except discord.HTTPException:
            await message.reply(text)

    # Reactions

    async def handle_reaction(self, message, item: Item, emoji: str, user):
        approve = emoji == YES
        if item.kind == "auto_fix" and not approve:
            claim_id = item.data.claim.claim_id
            overrides.record(claim_id, {}, no_auto_fix=True)
            log_decision(user, item, "undo")
            await self.recheck(message, claim_id, "Undone: no auto fixes on this claim.")

        elif item.kind == "needs_person":
            result = item.data
            if approve:
                # ✅ = accept the suggested correction (if there is one) and send it back through the agent.
                changes = overrides.changes_between(result.claim, result.suggested_claim)
                if changes:
                    overrides.record(result.claim.claim_id, changes)
                log_decision(user, item, "accepted_fix" if changes else "handled", overrides.describe(changes))
                prefix = (f"Applied {overrides.describe(changes)}." if changes else
                          "Marked handled (no change to the claim, e.g. a visit was chased).")
                await self.recheck(message, result.claim.claim_id, prefix)
            else:
                log_decision(user, item, "disagree")
                await message.reply("Logged as a disagreement. Reply with the right values (e.g. `units 16`) "
                                    "to send the claim back through the checker.")

        elif item.kind == "data_issue":
            hold = item.data
            if approve and hold.suggestion:
                value = hold.suggestion.split()[0]   # "PY01 (Northwind Health Plan)" -> "PY01"
                add_spelling(hold.field, hold.value, value)
                log_decision(user, item, "mapping_added", f"{hold.value} -> {value}")
                await self.recheck(message, hold.claim_id, f"Added `{hold.value}` → `{value}` to mappings.yaml.")
            elif not approve:
                log_decision(user, item, "send_back")
                await message.reply("Sent back to the agency to fix at the source.")

        elif item.kind == "proposal":
            proposal = item.data
            failure = propose.gate_failure(proposal["evidence"])
            if approve and failure:   # belt and braces: proposals.json could be edited by hand
                log_decision(user, item, "rule_blocked")
                await message.reply(f"Can't add this rule: {failure}.")
            elif approve:
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
            if hold.field in ("payer", "service_code", "modifier") and hold.value:
                # A new spelling is worth remembering for every future claim.
                add_spelling(hold.field, hold.value, text)
                await self.recheck(message, hold.claim_id, f"Mapped `{hold.value}` → `{text}`.")
            elif hold.field in overrides.EDITABLE:
                overrides.record(hold.claim_id, {hold.field: text})
                await self.recheck(message, hold.claim_id, f"Set {hold.field} = `{text}` on this claim.")
            else:
                await message.reply("Noted. The claim goes back to the agency with your correction.")

        elif item.kind in ("needs_person", "auto_fix"):
            claim_id = item.data.claim.claim_id
            try:
                changes = overrides.parse(text)
                overrides.record(claim_id, changes)
            except ValueError as e:
                await message.reply(f"Couldn't read that: {e}. Example: `units 16, auth AUTH10782`.")
                return
            await self.recheck(message, claim_id, f"Applied {overrides.describe(changes)}.")

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
                await self.post_one(message.channel, "proposal", new)
        else:
            await message.reply("Noted.")

    # Sending a claim back through the agent

    async def recheck(self, message, claim_id: str, prefix: str):
        """Re-run the agent after a person's action, report the claim's new outcome and any
        other claims the ledger moved, and post a fresh card if it still needs someone."""
        changed = await asyncio.to_thread(self.pipeline.refresh)
        p = self.pipeline
        lines = [f"{prefix} Re-checked **{claim_id}**: {self.outcome_text(claim_id)}"]
        ripple = {cid: change for cid, change in changed.items() if cid != claim_id}
        if ripple:
            shown = [f"{cid} {old} → {new}" for cid, (old, new) in sorted(ripple.items())[:5]]
            more = f" (+{len(ripple) - 5} more)" if len(ripple) > 5 else ""
            lines.append(f"Ledger knock-on: {'; '.join(shown)}{more}")
        await message.reply("\n".join(lines)[:MAX_MESSAGE])

        result, holds = p.run.result(claim_id), p.run.holds_for(claim_id)
        if holds:
            for hold in holds:
                await self.post_one(message.channel, "data_issue", hold)
        elif result and result.outcome == "needs_person":
            await self.post_one(message.channel, "needs_person", result)
        elif result and result.outcome in ("auto_fixed", "do_not_submit"):
            await self.post_one(message.channel, "auto_fix", result)

    def outcome_text(self, claim_id: str) -> str:
        run = self.pipeline.run
        result = None if run.holds_for(claim_id) else run.result(claim_id)
        icon = OUTCOME_ICONS.get(result.outcome, "") if result else "🧩"
        more = " New card below." if result and result.outcome == "needs_person" else ""
        return f"{icon} {run.status(claim_id)}{more}".strip()


def main(args):
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set the DISCORD_TOKEN environment variable (your Discord bot token).")
    ReviewBot(Pipeline(args.extra)).run(token)
