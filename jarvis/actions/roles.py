"""Role creation and configuration."""

from __future__ import annotations

import discord

from ..ai.tools import Risk, action, tool
from ..util import (
    parse_colour,
    parse_permissions,
    permissions_to_names,
    truncate,
)
from .helpers import (
    audit_reason,
    boolean,
    fail,
    int_between,
    role_hierarchy_error,
    role_or_error,
    sent,
)

ROLE_ACTIONS = {
    "list": action(Risk.SAFE),
    "info": action(Risk.SAFE),
    "members": action(Risk.SAFE),
    "create": action(Risk.MEDIUM, permission="manage_roles", confirm=False),
    "delete": action(Risk.HIGH, permission="manage_roles", confirm=True),
    "rename": action(Risk.MEDIUM, permission="manage_roles", confirm=False),
    "colour": action(Risk.LOW, permission="manage_roles", confirm=False),
    "hoist": action(Risk.LOW, permission="manage_roles", confirm=False),
    "mentionable": action(Risk.LOW, permission="manage_roles", confirm=False),
    "permissions": action(Risk.HIGH, permission="manage_roles", confirm=True),
    "position": action(Risk.MEDIUM, permission="manage_roles", confirm=False),
}

ROLE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ROLE_ACTIONS)},
        "role": {"type": "string", "description": "Role name, mention, or ID"},
        "name": {"type": "string", "description": "Name for a new/renamed role"},
        "colour": {
            "type": "string",
            "description": "Colour name (red, teal, blurple, …) or hex value such as #5865F2",
        },
        "hoist": {"type": "boolean", "description": "Show the role separately in the member list"},
        "mentionable": {"type": "boolean", "description": "Allow members to mention the role"},
        "permissions": {
            "type": "string",
            "description": "Comma-separated permission names, e.g. 'kick_members,manage_messages'",
        },
        "position": {"type": "integer", "description": "Role position (higher is above lower roles)"},
        "limit": {"type": "integer", "description": "Maximum members to list (default 50)"},
    },
    "required": ["action"],
}


def _role_line(role: discord.Role) -> str:
    colour = f"#{role.colour.value:06X}" if role.colour.value else "none"
    flags = []
    if role.hoist:
        flags.append("hoisted")
    if role.mentionable:
        flags.append("mentionable")
    if role.managed:
        flags.append("managed")
    suffix = f" · {', '.join(flags)}" if flags else ""
    return (
        f"{role.mention} — `{role.id}` · position {role.position} · "
        f"{len(role.members)} member(s) · colour {colour}{suffix}"
    )


def _role_edit_error(ctx, role: discord.Role) -> str | None:
    error = role_hierarchy_error(ctx, role)
    return error.summary if error is not None else None


@tool(
    "manage_roles",
    "Inspect, create, delete, rename, recolour, hoist, make mentionable, change permissions or "
    "reorder server roles, and list the members who have a role.",
    ROLE_SCHEMA,
    category="server",
    actions=ROLE_ACTIONS,
)
async def manage_roles(ctx, args):
    operation = str(args["action"]).lower()
    guild = ctx.guild

    if operation == "list":
        roles = sorted(guild.roles, key=lambda role: role.position, reverse=True)
        lines = [_role_line(role) for role in roles if not role.is_default()]
        embed = discord.Embed(
            title=f"🎭 Roles in {guild.name} ({len(guild.roles)})",
            description=truncate("\n".join(lines) or "No custom roles.", 4096),
            colour=discord.Colour.blurple(),
        )
        await ctx.say(embed=embed)
        return sent("Listed roles.")

    if operation == "create":
        name = str(args.get("name") or "").strip()[:100]
        if len(name) < 1:
            return fail("Give me a name for the new role, sir.")
        if not guild.me or not guild.me.guild_permissions.manage_roles:
            return fail("I need the **Manage Roles** permission to create roles, sir.")
        permissions = parse_permissions(args.get("permissions")) if args.get("permissions") else None
        colour = parse_colour(args.get("colour"), discord.Colour.default())
        try:
            role = await guild.create_role(
                name=name,
                colour=colour,
                hoist=boolean(args.get("hoist")),
                mentionable=boolean(args.get("mentionable")),
                permissions=permissions or discord.Permissions.none(),
                reason=audit_reason(ctx, f"Role created: {name}"),
            )
        except discord.HTTPException as exc:
            return fail(f"Discord refused to create the role: {truncate(exc, 180)}")
        await ctx.say(f"🎭 Created role {role.mention}.")
        return sent(f"Created role {role.name}.")

    role, error = role_or_error(ctx, args.get("role") or args.get("name"))
    if error:
        return error

    if operation == "info":
        embed = discord.Embed(title=f"🎭 {role.name}", colour=role.colour or discord.Colour.blurple())
        embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
        embed.add_field(name="Position", value=str(role.position), inline=True)
        embed.add_field(name="Members", value=str(len(role.members)), inline=True)
        embed.add_field(name="Colour", value=f"#{role.colour.value:06X}", inline=True)
        embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name="Managed", value="Yes" if role.managed else "No", inline=True)
        embed.add_field(name="Default", value="Yes" if role.is_default() else "No", inline=True)
        names = permissions_to_names(role.permissions)
        embed.add_field(
            name=f"Permissions ({len(names)})",
            value=truncate(", ".join(names) or "None", 1024),
            inline=False,
        )
        await ctx.say(embed=embed)
        return sent(f"Showed role {role.name}.")

    if operation == "members":
        limit = int_between(args.get("limit"), 1, 200, 50)
        members = list(role.members)
        lines = [f"{member.mention} — {member.display_name}" for member in members[:limit]]
        embed = discord.Embed(
            title=f"🎭 Members with {role.name} ({len(members)})",
            description=truncate("\n".join(lines) or "No members have this role.", 4096),
            colour=role.colour or discord.Colour.blurple(),
        )
        if len(members) > limit:
            embed.set_footer(text=f"Showing {limit} of {len(members)} members.")
        await ctx.say(embed=embed)
        return sent(f"Listed members with {role.name}.")

    hierarchy = _role_edit_error(ctx, role)
    if hierarchy:
        return fail(hierarchy)

    if operation == "delete":
        name = role.name
        try:
            await role.delete(reason=audit_reason(ctx, f"Role deleted: {name}"))
        except discord.HTTPException as exc:
            return fail(f"Discord refused to delete the role: {truncate(exc, 180)}")
        await ctx.say(f"🗑️ Deleted role **{name}**.")
        return sent(f"Deleted role {name}.")

    if operation == "rename":
        name = str(args.get("name") or "").strip()[:100]
        if not name:
            return fail("Give me the new role name, sir.")
        await role.edit(name=name, reason=audit_reason(ctx, "Role renamed"))
        await ctx.say(f"🎭 Renamed the role to **{name}**.")
        return sent("Role renamed.")

    if operation == "colour":
        colour = parse_colour(args.get("colour"), role.colour)
        await role.edit(colour=colour, reason=audit_reason(ctx, "Role colour changed"))
        await ctx.say(f"🎨 Set {role.mention} to **#{colour.value:06X}**.")
        return sent("Role colour updated.")

    if operation == "hoist":
        value = boolean(args.get("hoist"), not role.hoist)
        await role.edit(hoist=value, reason=audit_reason(ctx, "Role hoist changed"))
        await ctx.say(f"🎭 {role.mention} is {'now' if value else 'no longer'} shown separately.")
        return sent("Role hoist updated.")

    if operation == "mentionable":
        value = boolean(args.get("mentionable"), not role.mentionable)
        await role.edit(mentionable=value, reason=audit_reason(ctx, "Role mentionability changed"))
        await ctx.say(f"🎭 {role.mention} is {'now' if value else 'no longer'} mentionable.")
        return sent("Role mentionability updated.")

    if operation == "permissions":
        permissions = parse_permissions(args.get("permissions"))
        await role.edit(
            permissions=permissions,
            reason=audit_reason(ctx, "Role permissions changed"),
        )
        names = permissions_to_names(permissions)
        await ctx.say(
            f"🔐 Updated permissions for {role.mention}: "
            f"{truncate(', '.join(names) or 'none', 500)}"
        )
        return sent("Role permissions updated.")

    if operation == "position":
        position = int_between(args.get("position"), 1, max(1, len(guild.roles) - 1), role.position)
        max_position = guild.me.top_role.position - 1
        if max_position > 0:
            position = min(position, max_position)
        try:
            await role.edit(position=position, reason=audit_reason(ctx, "Role reordered"))
        except discord.HTTPException as exc:
            return fail(f"Discord refused to move the role: {truncate(exc, 180)}")
        await ctx.say(f"↕️ Moved {role.mention} to position **{position}**.")
        return sent("Role position updated.")

    return fail(f"I don't know how to `{operation}` roles, sir.")
