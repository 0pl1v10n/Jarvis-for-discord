"""The one place where AI-requested actions are authorized and executed."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

from .ai.tools import ActionMeta, Risk, ToolResult, ToolSpec, coerce_args
from .permissions import authorize
from .util import truncate

log = logging.getLogger(__name__)


class ActionRunner:
    def __init__(self, bot):
        self.bot = bot

    async def execute(self, ctx, spec: ToolSpec, args: dict[str, Any]) -> ToolResult:
        schema = spec.parameters or {}
        args = coerce_args(schema, args or {})

        action: str | None = None
        if spec.actions:
            action = str(args.get("action") or spec.default_action or "").strip().lower()
            if not action:
                return ToolResult.error(
                    f"Please specify an action for `{spec.name}`. Allowed: {', '.join(spec.actions)}."
                )
            if action not in spec.actions:
                return ToolResult.error(
                    f"`{action}` is not a valid action for `{spec.name}`. "
                    f"Allowed: {', '.join(spec.actions)}."
                )

        if spec.requires_guild and ctx.guild is None:
            return ToolResult.error("That action only works inside a server, sir.")

        meta = spec.meta_for(action)
        decision = authorize(ctx, meta)
        approver = None

        if not decision.allowed:
            if decision.needs_approval and ctx.guild is not None:
                description = self._describe(spec, action, args, ctx)
                approved, approver = await ctx.bot.approvals.request(
                    ctx,
                    "Approval needed",
                    description,
                    meta.permission,
                    risk=meta.risk,
                )
                if not approved:
                    return ToolResult.error("That request was not approved, sir.")
            else:
                return ToolResult.error(decision.reason)

        if meta.confirm:
            description = self._describe(spec, action, args, ctx)
            approved = await ctx.bot.confirmations.confirm(
                ctx,
                "Please confirm",
                description,
                risk=meta.risk,
            )
            if not approved:
                return ToolResult.error("Cancelled, sir.")

        try:
            result = await asyncio.wait_for(
                spec.handler(ctx, args),
                timeout=float(90),
            )
        except asyncio.TimeoutError:
            result = ToolResult.error("That took too long and was cancelled, sir.")
        except discord.Forbidden:
            result = ToolResult.error("Discord refused the action — I'm missing a permission, sir.")
        except discord.HTTPException as exc:
            result = ToolResult.error(f"Discord rejected that action, sir: {truncate(exc, 200)}")
        except (ValueError, KeyError, TypeError) as exc:
            result = ToolResult.error(f"I couldn't do that, sir: {truncate(exc, 250)}")
        except Exception as exc:
            log.exception("Tool %s failed", spec.name)
            result = ToolResult.error(f"I ran into a problem, sir: {truncate(exc, 250)}")

        if not isinstance(result, ToolResult):
            if isinstance(result, str):
                result = ToolResult.success(result, visible=True)
            elif result is None:
                result = ToolResult.success("Done.")
            else:
                result = ToolResult.success(str(result), visible=True)

        await self.bot.record_action(
            ctx,
            spec=spec,
            action=action or spec.name,
            args=args,
            result=result,
            approver=approver,
            risk=meta.risk,
        )
        return result

    def _describe(self, spec: ToolSpec, action: str | None, args: dict[str, Any], ctx) -> str:
        bits: list[str] = [f"**{spec.name.replace('_', ' ').title()}**"]
        if action:
            bits.append(f"`{action}`")

        for key in (
            "user",
            "member",
            "role",
            "channel",
            "name",
            "nickname",
            "reason",
            "duration",
            "minutes",
            "seconds",
            "amount",
            "limit",
            "prize",
            "title",
            "url",
            "message",
            "text",
            "topic",
        ):
            value = args.get(key)
            if value not in (None, "", []):
                bits.append(f"{key}: `{truncate(discord.utils.escape_markdown(str(value)), 140)}`")

        if action in {"nuke", "delete", "ban", "kick", "purge", "bulk_delete", "delete_role", "delete_channel"}:
            bits.append("\n⚠️ **This cannot be undone.**")
        return " ".join(bits)


__all__ = ["ActionRunner"]
