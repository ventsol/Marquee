"""Discord UI: trailer embeds + the persistent genre role-menu buttons."""

from __future__ import annotations

import logging

import discord

import config
import db
from youtube import Trailer

log = logging.getLogger("trailer.views")

# Discord limits a view to 25 components total.
MAX_MENU_BUTTONS = 24


# ── Embeds ───────────────────────────────────────────────────────────────────
def build_trailer_embed(trailer: Trailer, *, kind_label: bool = True) -> discord.Embed:
    """Build the announcement embed for one trailer."""
    primary = trailer.genres[0] if trailer.genres else None
    color = config.genre_color(primary) if primary else config.DEFAULT_GENRE_COLOR

    kind_titles = {
        "trailer": "\U0001F3AC New Trailer",
        "red-band": "\U0001F3AC New Red-Band Trailer",
        "final": "\U0001F3AC Final Trailer",
        "teaser": "\U0001F3AC New Teaser",
        "clip": "\U0001F39E\uFE0F New Clip",
        "other": "\U0001F4FD\uFE0F New from",
    }
    heading = kind_titles.get(trailer.kind, "\U0001F3AC New Trailer")
    title = f"{heading}: {trailer.title}" if kind_label else trailer.title

    embed = discord.Embed(
        title=title[:256],
        url=trailer.url,
        color=color,
        timestamp=trailer.published_at,
    )
    embed.set_author(name=trailer.studio)

    if trailer.thumbnail:
        embed.set_image(url=trailer.thumbnail)

    if trailer.genres:
        tags = " ".join(
            f"{config.genre_emoji(g)} {config.genre_label(g)}" for g in trailer.genres
        )
        embed.add_field(name="Genres", value=tags[:1024], inline=False)

    embed.add_field(
        name="Watch",
        value=f"[youtu.be/{trailer.video_id}](https://youtu.be/{trailer.video_id})",
        inline=True,
    )
    embed.set_footer(text=f"{trailer.studio} \u2022 YouTube")
    return embed


def build_menu_embed(guild: discord.Guild, genres: list[str]) -> discord.Embed:
    embed = discord.Embed(
        title="\U0001F3AC Trailer Genre Notifications",
        description=(
            "Click a button to toggle a genre on or off.\n"
            "You'll only be pinged for trailers matching your genres.\n\n"
            "No genres selected = you won't be pinged (trailers still post here)."
        ),
        color=config.DEFAULT_GENRE_COLOR,
    )
    listed = "\n".join(
        f"{config.genre_emoji(g)} **{config.genre_label(g)}**" for g in genres
    )
    if listed:
        embed.add_field(name="Available genres", value=listed[:1024], inline=False)
    embed.set_footer(text=f"{guild.name} \u2022 Marquee")
    return embed


# ── Persistent role menu ─────────────────────────────────────────────────────
class GenreToggleButton(discord.ui.Button):
    """One button per genre. custom_id survives restarts."""

    def __init__(self, genre: str):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=config.genre_label(genre)[:80],
            emoji=config.genre_emoji(genre),
            custom_id=f"trailer:genre:{genre}",
            row=None,
        )
        self.genre = genre

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this in a server, not a DM.", ephemeral=True
            )
            return

        mapping = await db.genre_roles(interaction.guild.id)
        role_id = mapping.get(self.genre)
        role = interaction.guild.get_role(role_id) if role_id else None

        if role is None:
            await interaction.response.send_message(
                f"The **{config.genre_label(self.genre)}** role is missing. "
                "An admin should re-run `/genres`.",
                ephemeral=True,
            )
            return

        member = interaction.user
        assert isinstance(member, discord.Member)

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Marquee genre toggle")
                msg = f"Removed **{role.name}** \u2014 you won't be pinged for these."
            else:
                await member.add_roles(role, reason="Marquee genre toggle")
                msg = f"Added **{role.name}** \u2014 you'll be pinged for these."
        except discord.Forbidden:
            await interaction.response.send_message(
                "I can't manage that role. It may be above my highest role.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(msg, ephemeral=True)

        # Recolour the button to reflect the member's current state.
        roles_by_genre = {g: interaction.guild.get_role(r) for g, r in mapping.items()}
        for child in self.view.children:
            if isinstance(child, GenreToggleButton):
                target = roles_by_genre.get(child.genre)
                child.style = (
                    discord.ButtonStyle.success
                    if target and target in member.roles
                    else discord.ButtonStyle.secondary
                )
        try:
            await interaction.message.edit(view=self.view)  # type: ignore[union-attr]
        except discord.HTTPException:
            pass


class GenreMenuView(discord.ui.View):
    """Persistent view. timeout=None so it never expires."""

    def __init__(self, genres: list[str]):
        super().__init__(timeout=None)
        for i, genre in enumerate(genres[:MAX_MENU_BUTTONS]):
            self.add_item(GenreToggleButton(genre))


def register_persistent_views(bot: discord.Client, genres: list[str]) -> None:
    """Re-attach the view on startup so old button messages keep working."""
    bot.add_view(GenreMenuView(genres))
    log.info("Registered persistent genre menu (%d genres)", len(genres[:MAX_MENU_BUTTONS]))


# ── Role provisioning ────────────────────────────────────────────────────────
async def ensure_genre_roles(
    guild: discord.Guild, genres: list[str]
) -> tuple[dict[str, discord.Role], list[str]]:
    """Create missing genre roles. Returns (genre->role, created names)."""
    existing = await db.genre_roles(guild.id)
    roles: dict[str, discord.Role] = {}
    created: list[str] = []

    me = guild.me
    if me is None:
        return roles, created

    for genre in genres:
        role_id = existing.get(genre)
        role = guild.get_role(role_id) if role_id else None

        if role is None:
            # Reuse an identically named role if one already exists.
            wanted = config.genre_label(genre)
            role = discord.utils.find(
                lambda r, w=wanted: r.name.lower() == w.lower(), guild.roles
            )

        if role is None:
            try:
                role = await guild.create_role(
                    name=config.genre_label(genre),
                    color=discord.Color(config.genre_color(genre)),
                    mentionable=True,
                    hoist=False,
                    reason="Marquee genre role setup",
                )
                created.append(role.name)
            except discord.Forbidden:
                log.warning("Guild %s: cannot create roles", guild.id)
                continue
            except discord.HTTPException as exc:
                log.error("Guild %s: role create failed: %s", guild.id, exc)
                continue

        roles[genre] = role
        await db.set_genre_role(guild.id, genre, role.id)

    return roles, created
