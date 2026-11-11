"""Permission policy for AI-requested actions."""

from __future__ import annotations

from dataclasses import dataclass

import discord

from .ai.tools import ActionMeta, Risk


LEVEL_TEXT = {
    Risk.SAFE: "everyone",
    Risk.LOW: "a member",
    Risk.MEDIUM: "a moderator",
    Risk.HIGH: "an administrator",
    Risk.CRITICAL: "the server owner",
}


@dataclass(slots=True)
class AuthDecision:
    allowed: bool
    reason: str = ""
    needs_approval: bool = False
    permission: str | tuple[str, ...] | None = None


def _perm_names(permission) -> tuple[str, ...]:
    if not permission:
        return ()
    if isinstance(permission, str):
        return (permission,)
    return tuple(permission)


def is_owner(member: discord.Member | None, guild: discord.Guild | None) -> bool:
    if member is None:
        return False
    if guild is not None and member.id == guild.owner_id:
        return True
    return False


def qualifies(
    member: discord.Member | None,
    permission: str | tuple[str, ...] | None,
    *,
    guild: discord.Guild | None = None,
    settings=None,
) -> bool:
    if not permission:
        return True
    if member is None:
        return False
    if guild is not None and member.id == guild.owner_id:
        return True
    if settings is not None and member.id in settings.owner_ids:
        return True

    perms = getattr(member, "guild_permissions", None)
    if perms is not None and perms.administrator:
        return True
    if perms is None:
        return False
    return any(bool(getattr(perms, name, False)) for name in _perm_names(permission))


def authorize(ctx, meta: ActionMeta) -> AuthDecision:
    """Check whether the requester may perform this action."""
    if meta.owner_only:
        if ctx.is_owner:
            return AuthDecision(True)
        return AuthDecision(False, "That action is restricted to the bot/server owner, sir.")

    if not meta.permission:
        return AuthDecision(True)

    if ctx.author is None:
        return AuthDecision(False, "I can't verify your permissions in a DM, sir.")

    if ctx.is_owner or ctx.is_admin:
        return AuthDecision(True, permission=meta.permission)

    if ctx.can(meta.permission):
        return AuthDecision(True, permission=meta.permission)

    if meta.approval and ctx.guild is not None:
        return AuthDecision(
            False,
            f"Approval from {LEVEL_TEXT.get(meta.risk, 'a moderator')} is required, sir.",
            needs_approval=True,
            permission=meta.permission,
        )

    needed = ", ".join(_perm_names(meta.permission)).replace("_", " ")
    return AuthDecision(False, f"You need the **{needed}** permission for that, sir.")


def approver_qualifies(
    member: discord.Member | None,
    permission: str | tuple[str, ...] | None,
    *,
    guild: discord.Guild | None = None,
    settings=None,
) -> bool:
    return qualifies(member, permission, guild=guild, settings=settings)


def risk_label(risk: Risk) -> str:
    return {
        Risk.SAFE: "🟢 Safe",
        Risk.LOW: "🔵 Low",
        Risk.MEDIUM: "🟡 Moderate",
        Risk.HIGH: "🟠 High",
        Risk.CRITICAL: "🔴 Critical",
    }.get(risk, str(risk))


def approval_roles(settings, permission) -> tuple[int, int]:
    """Return (role_id_to_ping, admin_role_id) based on the permission."""
    names = _perm_names(permission)
    admin_perms = {
        "administrator",
        "manage_guild",
        "ban_members",
        "manage_roles",
        "manage_channels",
        "manage_webhooks",
    }
    if any(name in admin_perms for name in names):
        return settings.admin_role_id, settings.admin_role_id
    return settings.mod_role_id, settings.admin_role_id
