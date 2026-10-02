"""Discord client construction, event wiring, and slash commands.

Split out of `bot.py` so the web app can run the bot in the same event loop
instead of shelling out. `bot.py` remains a thin standalone entry point.

Everything here is safe to import without a token and without connecting.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import poller
import runtime as rt
import views

log = logging.getLogger("trailer.core")


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True  # required to add/remove roles
    return intents


def create_bot() -> commands.Bot:
    """Construct a configured (but not connected) bot with all commands."""
    bot = commands.Bot(command_prefix="!trailer ", intents=build_intents())
    register_commands(bot)
    register_events(bot)
    rt.RUNTIME.attach_bot(bot)
    return bot


# ── Helpers ──────────────────────────────────────────────────────────────────
def missing_role_perms(guild: discord.Guild) -> list[str]:
    """Return the permissions the bot is missing in a guild."""
    me = guild.me
    if me is None:
        return ["bot member not cached"]
    perms = me.guild_permissions
    needed = {
        "Manage Roles": perms.manage_roles,
        "Send Messages": perms.send_messages,
        "Embed Links": perms.embed_links,
    }
    return [name for name, ok in needed.items() if not ok]


async def target_channel(guild: discord.Guild) -> discord.abc.Messageable | None:
    channel_id = await db.get_announce_channel(guild.id)
    if channel_id is None:
        return None
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.abc.Messageable):
        return channel
    return None


def genre_choices() -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=config.genre_label(g), value=g)
        for g in config.REGISTRY.all_genres[:25]
    ]


# ── One-time setup (idempotent) ──────────────────────────────────────────────
async def on_ready_setup(bot: commands.Bot) -> None:
    """DB connect, persistent views, command sync, scheduler start.

    Called from on_ready. Guarded so a re-connect doesn't double-start the
    scheduler or re-sync commands.
    """
    await db.connect()
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
    log.info(
        "Watching %d studios across %d guild(s)",
        len(config.REGISTRY), len(bot.guilds),
    )

    # Persistent views must be re-registered every boot.
    views.register_persistent_views(bot, config.REGISTRY.all_genres)

    try:
        if config.DEV_GUILD_IDS:
            for guild_id in config.DEV_GUILD_IDS:
                guild = discord.Object(id=guild_id)
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                log.info("Synced %d commands to dev guild %s", len(synced), guild_id)
        else:
            synced = await bot.tree.sync()
            log.info("Synced %d global commands", len(synced))
    except discord.HTTPException as exc:
        log.error("Command sync failed: %s", exc)

    if rt.RUNTIME.scheduler is None:
        rt.RUNTIME.scheduler = poller.start_scheduler(bot)
        # Catch up on anything posted while we were offline.
        bot.loop.create_task(poller.poll_once(bot, announce=True, trigger="startup"))

    rt.RUNTIME.ready = True


# ── Events ───────────────────────────────────────────────────────────────────
def register_events(bot: commands.Bot) -> None:
    @bot.event
    async def on_ready():
        await on_ready_setup(bot)

    @bot.event
    async def on_guild_join(guild: discord.Guild):
        log.info("Joined guild %s (%s)", guild.name, guild.id)
        missing = missing_role_perms(guild)
        if missing:
            log.warning("Guild %s missing permissions: %s", guild.id, ", ".join(missing))

    @bot.event
    async def on_guild_remove(guild: discord.Guild):
        log.info("Removed from guild %s (%s)", guild.name, guild.id)


# ── Commands ─────────────────────────────────────────────────────────────────
def register_commands(bot: commands.Bot) -> None:
    @bot.tree.command(
        name="setup", description="Set the channel where trailers get posted"
    )
    @app_commands.describe(channel="The text channel for trailer announcements")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setup_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True, thinking=True)

        missing = missing_role_perms(interaction.guild)
        if missing:
            await interaction.followup.send(
                f"\u26A0\uFE0F I'm missing these permissions: **{', '.join(missing)}**. "
                "Grant them and re-run `/setup`.",
                ephemeral=True,
            )
            return

        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            await interaction.followup.send(
                f"I can't post embeds in {channel.mention}. Check channel permissions.",
                ephemeral=True,
            )
            return

        await db.set_announce_channel(interaction.guild.id, channel.id)
        await interaction.followup.send(
            f"\u2705 Trailers will be posted to {channel.mention}.\n"
            "Next: run `/genres` in that channel to create the role menu.",
            ephemeral=True,
        )

    @bot.tree.command(
        name="genres", description="Create genre roles and post the role-toggle menu"
    )
    @app_commands.describe(
        channel="Channel for the role menu (defaults to the announce channel)"
    )
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.guild_only()
    async def genres_cmd(
        interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Use this in a server.", ephemeral=True)
            return

        target = channel
        if target is None:
            existing = await target_channel(guild)
            target = (
                existing
                if isinstance(existing, discord.TextChannel)
                else interaction.channel
            )

        if not isinstance(target, discord.TextChannel):
            await interaction.followup.send(
                "Couldn't resolve a text channel for the menu.", ephemeral=True
            )
            return

        genres = config.REGISTRY.all_genres
        roles, created = await views.ensure_genre_roles(guild, genres)

        if not roles:
            await interaction.followup.send(
                "\u274C Couldn't create any roles. I need the **Manage Roles** "
                "permission, and my highest role must sit above the genre roles.",
                ephemeral=True,
            )
            return

        active = [g for g in genres if g in roles]
        view = views.GenreMenuView(active)
        embed = views.build_menu_embed(guild, active)

        try:
            message = await target.send(embed=embed, view=view)
        except discord.Forbidden:
            await interaction.followup.send(
                f"I can't post in {target.mention}.", ephemeral=True
            )
            return

        await db.add_role_menu(guild.id, target.id, message.id)

        summary = f"\u2705 Menu posted in {target.mention} with **{len(active)}** genres."
        if created:
            summary += f"\nCreated {len(created)} role(s): {', '.join(created[:10])}"
        summary += (
            "\n\nMembers click a button to toggle a genre. Tip: in Server Settings "
            "\u2192 Roles, hide these roles so they're opt-in, not visible to all."
        )
        await interaction.followup.send(summary, ephemeral=True)

    @bot.tree.command(
        name="latest", description="Show the most recent trailers the bot has seen"
    )
    @app_commands.describe(
        count="How many to show (1-10)",
        genre="Only trailers from studios tagged with this genre",
    )
    @app_commands.guild_only()
    async def latest_cmd(
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 10] = 5,
        genre: str | None = None,
    ):
        await interaction.response.defer(thinking=True)

        studios = {
            s.name
            for s in (config.REGISTRY.by_genre(genre) if genre else config.REGISTRY)
        }
        if genre and not studios:
            await interaction.followup.send(
                f"No studios are tagged `{genre}`. Try `/studios`.", ephemeral=True
            )
            return

        rows = await db.recent_seen(limit=count * 3 if genre else count)
        selected = [r for r in rows if r["studio"] in studios][:count]

        if not selected:
            await interaction.followup.send(
                "No trailers ingested yet. Run `/refresh` and try again.", ephemeral=True
            )
            return

        embeds = []
        for row in selected:
            embed = discord.Embed(
                title=row["title"][:256],
                url=f"https://youtu.be/{row['video_id']}",
                color=config.DEFAULT_GENRE_COLOR,
                timestamp=datetime.fromisoformat(row["published_at"]),
            )
            embed.set_author(name=row["studio"])
            embed.set_thumbnail(
                url=f"https://i.ytimg.com/vi/{row['video_id']}/mqdefault.jpg"
            )
            embeds.append(embed)

        header = f"\U0001F4FD\uFE0F Latest {len(embeds)}"
        if genre:
            header += f" {config.genre_label(genre)}"
        await interaction.followup.send(header, embeds=embeds)

    @bot.tree.command(
        name="studios", description="List configured studios and their genres"
    )
    @app_commands.describe(genre="Filter to one genre")
    async def studios_cmd(interaction: discord.Interaction, genre: str | None = None):
        await interaction.response.defer(ephemeral=True, thinking=True)

        studios = config.REGISTRY.by_genre(genre) if genre else list(config.REGISTRY)
        if not studios:
            await interaction.followup.send(
                "No studios match that filter.", ephemeral=True
            )
            return

        embeds: list[discord.Embed] = []
        chunk: list[str] = []
        size = 0
        lines = [
            f"**{s.name}** \u2014 "
            f"{', '.join(config.genre_label(g) for g in s.genres) or 'untagged'}"
            for s in studios
        ]
        for line in lines:
            if size + len(line) + 1 > 1000:
                embeds.append(_studios_embed(chunk, len(studios), genre))
                chunk, size = [], 0
            chunk.append(line)
            size += len(line) + 1
        if chunk:
            embeds.append(_studios_embed(chunk, len(studios), genre))

        await interaction.followup.send(embeds=embeds[:10], ephemeral=True)

    @bot.tree.command(name="refresh", description="Poll all studio feeds right now")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def refresh_cmd(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if rt.RUNTIME.poll_in_progress:
            await interaction.followup.send(
                "\u23F3 A poll is already running.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"\U0001F504 Polling {len(config.REGISTRY)} studio feeds\u2026", ephemeral=True
        )
        result = await poller.poll_once(bot, announce=True, trigger="manual")

        lines = [
            f"Fetched **{result.fetched}** item(s) after dedupe.",
            f"New: **{result.new}** \u2022 Announced: **{result.announced}**",
        ]
        if result.seeded:
            lines.append(
                f"_Seeded {result.seeded} backlog item(s) silently (fresh database)._"
            )
        if result.errors:
            lines.append(
                f"\u26A0\uFE0F {len(result.errors)} error(s): "
                + "; ".join(result.errors[:3])[:400]
            )

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @bot.tree.command(
        name="unsubscribe", description="Stop all trailer pings for yourself"
    )
    @app_commands.guild_only()
    async def unsubscribe_cmd(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("Use this in a server.", ephemeral=True)
            return

        mapping = await db.genre_roles(guild.id)
        roles = [r for r in (guild.get_role(rid) for rid in mapping.values()) if r]
        member = interaction.user
        assert isinstance(member, discord.Member)
        to_remove = [r for r in roles if r in member.roles]
        if not to_remove:
            await interaction.followup.send(
                "You have no genre roles set.", ephemeral=True
            )
            return
        try:
            await member.remove_roles(*to_remove, reason="Marquee unsubscribe")
        except discord.Forbidden:
            await interaction.followup.send(
                "I can't manage those roles (they may be above mine).", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"\u2705 Removed {len(to_remove)} genre role(s).", ephemeral=True
        )

    @bot.tree.command(name="status", description="Show Marquee health and stats")
    async def status_cmd(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        count = await db.count_seen()
        guilds = await db.all_guild_configs()

        embed = discord.Embed(
            title="\U0001F3AC Marquee status",
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Studios watched", value=str(len(config.REGISTRY)), inline=True
        )
        embed.add_field(name="Trailers tracked", value=str(count), inline=True)
        embed.add_field(name="Guilds configured", value=str(len(guilds)), inline=True)
        embed.add_field(
            name="Poll interval", value=f"{config.POLL_INTERVAL_MINUTES} min", inline=True
        )
        embed.add_field(
            name="Scheduler",
            value="running" if rt.RUNTIME.scheduler else "stopped",
            inline=True,
        )
        embed.add_field(
            name="Latency", value=f"{rt.RUNTIME.latency_ms} ms", inline=True
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


def _studios_embed(
    lines: list[str], total: int, genre: str | None
) -> discord.Embed:
    title = f"\U0001F3E2 {total} studio(s)"
    if genre:
        title += f" tagged {config.genre_label(genre)}"
    return discord.Embed(
        title=title,
        description="\n".join(lines)[:4096],
        color=config.DEFAULT_GENRE_COLOR,
    )


# ── Embedded start/stop ──────────────────────────────────────────────────────
async def start_bot(bot: commands.Bot, token: str) -> None:
    """Connect the bot without blocking the running event loop."""
    log.info("Connecting bot to Discord gateway\u2026")
    await bot.start(token)


async def stop_bot(bot: commands.Bot | None) -> None:
    if bot is None:
        return
    if not bot.is_closed():
        log.info("Closing Discord connection\u2026")
        await bot.close()
