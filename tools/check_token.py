"""Verify the configured Discord token actually authenticates.

Usage:
    python -m tools.check_token

Exits 0 on success, 1 on a bad token. Prints the bot identity and guilds.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

import discord  # noqa: E402

import config  # noqa: E402


async def main() -> int:
    token = config.DISCORD_TOKEN
    if not token or token == "your-token-here":
        print("[X] DISCORD_TOKEN is not set in .env")
        return 1

    print(f"Token loaded: {len(token)} chars")
    print("Contacting Discord...\n")

    client = discord.Client(intents=discord.Intents.none())

    @client.event
    async def on_ready():
        print("[OK] Logged in successfully")
        print(f"     Bot user : {client.user}")
        print(f"     Bot ID   : {client.user.id}")
        print(f"     Guilds   : {len(client.guilds)}")
        for g in client.guilds:
            print(f"       - {g.name} ({g.id})")
        if not client.guilds:
            print("       (none - invite the bot to a server)")

        # Check the permissions it actually needs.
        for g in client.guilds:
            me = g.me
            if me is None:
                continue
            p = me.guild_permissions
            need = {
                "Manage Roles": p.manage_roles,
                "Send Messages": p.send_messages,
                "Embed Links": p.embed_links,
            }
            missing = [k for k, v in need.items() if not v]
            if missing:
                print(f"     [!] {g.name} missing: {', '.join(missing)}")
            else:
                print(f"     [OK] {g.name} has all required permissions")

            # Role hierarchy check.
            if me.top_role is not None:
                below = [
                    r.name
                    for r in g.roles
                    if r.position < me.top_role.position and not r.is_default()
                ]
                print(f"     Roles I can manage: {len(below)}")
                for name in ("Horror", "Anime", "Action"):
                    if name in [r.name for r in g.roles]:
                        r = discord.utils.get(g.roles, name=name)
                        ok = r.position < me.top_role.position
                        print(f"       Genre role '{name}' manageable: {ok}")

        await client.close()

    try:
        await client.start(token)
        return 0
    except discord.LoginFailure:
        print("[X] Improper token has been passed.")
        print("    Reset it in the Developer Portal -> Bot -> Reset Token,")
        print("    then re-run setup_test.bat.")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[X] Connection failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
