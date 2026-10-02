"""
Marquee web dashboard.

Runs the Discord bot, the poller, and the admin dashboard in a single asyncio
loop, so the UI can mutate config and trigger polls without any IPC.

    python app.py          # dashboard + bot + poller
    http://127.0.0.1:8000

Requires WEBAPP_PASSWORD in .env.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import botcore
import config
import config_store as cs
import db
import lockfile
import poller
import runtime as rt
from webapp import auth

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("trailer.web")

ROOT = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))


def _refuse_missing_password() -> None:
    """Fail loudly, with instructions that match where the app is running.

    The old message said "add it to .env", which is useless in a container
    where no .env exists. Detect the hosting context and say the right thing.
    """
    on_platform = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("PORT"))
    host = os.getenv("RAILWAY_SERVICE_NAME") or "your host"

    lines = ["", "=" * 62, "  WEBAPP_PASSWORD is not set. Refusing to start.", "=" * 62, ""]

    if on_platform:
        lines += [
            "  This looks like a hosted deployment, so there is no .env file.",
            "  Set it as an environment variable instead:",
            "",
            f"    Railway dashboard -> {host} -> Variables -> New Variable",
            "      WEBAPP_PASSWORD = <a long random string>",
            "",
            "  Or with the CLI, from the project directory:",
            "",
            '    railway variables --set "WEBAPP_PASSWORD=<value>"',
            "",
            "  Also set DISCORD_TOKEN if you have not already.",
        ]
    else:
        lines += [
            "  Create a .env file next to app.py, or run .\\setup_test.bat",
            "  which generates one for you.",
            "",
            "      WEBAPP_PASSWORD=<a long random string>",
        ]

    lines += [
        "",
        "  The dashboard can rewrite studios.toml and delete studios,",
        "  so it will not run without a password.",
        "=" * 62,
        "",
    ]

    for line in lines:
        print(line, flush=True)
    raise SystemExit(1)



# ── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not auth.password_configured():
        _refuse_missing_password()

    await db.connect()
    log.info("Database ready")

    bot_task = None
    if config.WEBAPP_RUN_BOT:
        if not lockfile.acquire("webapp"):
            log.error(
                "Another Marquee process is already running. Refusing to "
                "start a second poller. Stop it, or set WEBAPP_RUN_BOT=false "
                "to serve the dashboard only."
            )
            raise RuntimeError("Another Marquee process is already running")
        if not config.DISCORD_TOKEN:
            log.warning(
                "DISCORD_TOKEN not set — serving dashboard in config-only mode "
                "(no bot, no polling)."
            )
        else:
            bot = botcore.create_bot()
            bot_task = asyncio.create_task(botcore.start_bot(bot, config.DISCORD_TOKEN))
            rt.RUNTIME.bot_task = bot_task
            bot_task.add_done_callback(_on_bot_exit)
            log.info("Bot task started")
    else:
        log.info("WEBAPP_RUN_BOT=false — dashboard only, no bot in this process")

    try:
        yield
    finally:
        poller.stop_scheduler()
        if bot_task is not None:
            await botcore.stop_bot(rt.RUNTIME.bot)
            bot_task.cancel()
            try:
                await bot_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await db.close()
        log.info("Shutdown complete")


def _on_bot_exit(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("Bot task exited with error: %s", exc)
    else:
        log.warning("Bot task exited cleanly (unexpected)")


app = FastAPI(title="Marquee dashboard", version="1.0.0", lifespan=lifespan)

# Order matters. Starlette prepends each add_middleware call, so the LAST one
# added is the OUTERMOST. AuthMiddleware reads request.session, so
# SessionMiddleware must be added last to sit outside it.
app.add_middleware(auth.AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.WEBAPP_PASSWORD or "insecure-dev",
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


# ── Template helpers ─────────────────────────────────────────────────────────
def page(request: Request, name: str, **ctx) -> HTMLResponse:
    base = {
        "request": request,
        "studios_count": len(config.REGISTRY),
        "all_genres": config.REGISTRY.all_genres,
        "now": datetime.now(timezone.utc),
        "flash": request.query_params.get("flash"),
        "error": request.query_params.get("error"),
    }
    base.update(ctx)
    return TEMPLATES.TemplateResponse(request, name, base)


def redirect(path: str, **params) -> RedirectResponse:
    if params:
        from urllib.parse import urlencode

        path = f"{path}?{urlencode(params)}"
    return RedirectResponse(url=path, status_code=303)


# ── Auth routes ──────────────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return page(request, "login.html", next=auth.safe_next(request.query_params.get("next")))


@app.post("/login")
async def login_submit(
    request: Request,
    password: str = Form(...),
    next: str = Form("/"),
):
    if not auth.verify_password(password):
        log.warning("Failed login attempt from %s", request.client)
        await asyncio.sleep(0.5)  # slow down brute force
        return redirect("/login", error="Incorrect password.")

    auth.login_session(request)
    return redirect(auth.safe_next(next))


@app.post("/logout")
async def logout(request: Request):
    auth.logout_session(request)
    return redirect("/login", flash="Signed out.")


# ── Dashboard ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    status = rt.RUNTIME.as_dict()
    seen = await db.count_seen()
    guilds = await db.all_guild_configs()
    recent = await db.recent_seen(limit=8)

    return page(
        request,
        "index.html",
        status=status,
        seen_count=seen,
        guild_count=len(guilds),
        guilds=guilds,
        recent=recent,
        filter_mode=config.FILTER_MODE,
        poll_interval=config.POLL_INTERVAL_MINUTES,
        seed_quietly=config.SEED_QUIETLY,
        bot_enabled=config.WEBAPP_RUN_BOT,
    )


# ── Studios ──────────────────────────────────────────────────────────────────
@app.get("/studios", response_class=HTMLResponse)
async def studios_list(request: Request):
    genre_filter = request.query_params.get("genre")
    region_filter = request.query_params.get("region")
    q = (request.query_params.get("q") or "").strip().lower()

    rows = cs.studio_dicts()
    regions = sorted({r.get("region", "en") for r in rows})

    if genre_filter:
        rows = [r for r in rows if genre_filter in r.get("genres", [])]
    if region_filter:
        rows = [r for r in rows if r.get("region") == region_filter]
    if q:
        rows = [
            r
            for r in rows
            if q in r["name"].lower() or q in r["channel_id"].lower()
        ]

    return page(
        request,
        "studios.html",
        studios=rows,
        total=len(cs.studio_dicts()),
        genre_filter=genre_filter,
        region_filter=region_filter,
        q=q,
        regions=regions,
    )


@app.get("/studios/new", response_class=HTMLResponse)
async def studio_new(request: Request):
    return page(
        request,
        "studio_form.html",
        studio=cs.StudioDraft(),
        mode="new",
        issues=[],
    )


@app.get("/studios/{channel_id}/edit", response_class=HTMLResponse)
async def studio_edit(request: Request, channel_id: str):
    rows = cs.studio_dicts()
    match = next((r for r in rows if r["channel_id"] == channel_id), None)
    if match is None:
        return redirect("/studios", error=f"No studio with channel_id {channel_id}")

    draft = cs.StudioDraft(
        name=match["name"],
        channel_id=match["channel_id"],
        genres=list(match["genres"]),
        region=match["region"],
        original_channel_id=match["channel_id"],
    )
    return page(request, "studio_form.html", studio=draft, mode="edit", issues=[])


@app.post("/studios/save")
async def studio_save(
    request: Request,
    mode: str = Form("new"),
    name: str = Form(""),
    channel_id: str = Form(""),
    region: str = Form("en"),
    genres: list[str] = Form(default=[]),
    original_channel_id: str = Form(""),
    custom_genre: str = Form(""),
):
    # Free-text genre box, comma separated, merged with the chip selections.
    extra = [g.strip().lower() for g in custom_genre.split(",") if g.strip()]
    merged = sorted({g.strip().lower() for g in list(genres) + extra if g.strip()})

    draft = cs.StudioDraft(
        name=name,
        channel_id=channel_id.strip(),
        genres=merged,
        region=region,
        original_channel_id=original_channel_id or None,
    )

    issues = cs.validate_draft(draft)
    if cs.has_errors(issues):
        return page(
            request,
            "studio_form.html",
            studio=draft,
            mode=mode,
            issues=issues,
        )

    try:
        if mode == "edit":
            cs.update_studio(draft)
            verb = "Updated"
        else:
            cs.add_studio(draft)
            verb = "Added"
    except (ValueError, KeyError) as exc:
        return page(
            request,
            "studio_form.html",
            studio=draft,
            mode=mode,
            issues=[cs.ValidationIssue("channel_id", str(exc))],
        )

    log.info("%s studio %r", verb, draft.name)
    return redirect("/studios", flash=f"{verb} {draft.name}.")


@app.post("/studios/{channel_id}/delete")
async def studio_delete(request: Request, channel_id: str):
    try:
        cs.delete_studio(channel_id)
    except KeyError:
        return redirect("/studios", error="That studio no longer exists.")
    return redirect("/studios", flash=f"Deleted studio {channel_id}.")


@app.post("/api/studios/test")
async def api_test_feed(request: Request):
    """Live feed check for a channel ID, before saving it."""
    payload = await request.json()
    channel_id = (payload.get("channel_id") or "").strip()
    ok, message, count = await cs.check_feed(channel_id)
    return JSONResponse(
        {"ok": ok, "message": message, "count": count, "channel_id": channel_id}
    )


@app.post("/api/studios/bulk-test")
async def api_bulk_test(request: Request):
    """Check every configured studio's feed. Slow — the UI runs it on demand."""
    import youtube

    results = await youtube.validate_registry(config.REGISTRY)
    bad = [name for name, ok in results.items() if not ok]
    return JSONResponse(
        {
            "ok": not bad,
            "total": len(results),
            "healthy": len(results) - len(bad),
            "failing": bad,
        }
    )


# ── Genres ───────────────────────────────────────────────────────────────────
@app.get("/genres", response_class=HTMLResponse)
async def genres_page(request: Request):
    declared = config.REGISTRY.all_genres
    definitions = config.all_genre_definitions()
    rows = []
    for genre in declared:
        meta = definitions.get(genre) or {}
        rows.append(
            {
                "key": genre,
                "label": config.genre_label(genre),
                "color": f"#{config.genre_color(genre):06x}",
                "emoji": config.genre_emoji(genre),
                "custom": genre not in config.GENRES,
                "studios": [
                    s.name for s in config.REGISTRY if genre in s.genres
                ],
            }
        )
    unused = [g for g in definitions if g not in declared]
    return page(request, "genres.html", rows=rows, unused=unused)


@app.post("/genres/save")
async def genres_save(
    request: Request,
    genre_key: str = Form(...),
    label: str = Form(...),
    color: str = Form(...),
    emoji: str = Form(""),
):
    import genre_store

    key = genre_key.strip().lower()
    if key in config.GENRES:
        return redirect(
            "/genres",
            error=f"{key} is a built-in genre; edit GENRES in config.py to change it.",
        )

    try:
        hex_color = int(color.strip().lstrip("#"), 16)
    except ValueError:
        return redirect("/genres", error="Colour must be a hex value like #5865F2.")

    genre_store.set_genre(
        key,
        label=label.strip() or key.replace("-", " ").title(),
        color=hex_color,
        emoji=emoji.strip() or "\U0001F3AC",
    )
    return redirect("/genres", flash=f"Saved genre {key}.")


@app.post("/genres/{genre_key}/delete")
async def genres_delete(request: Request, genre_key: str):
    import genre_store

    try:
        genre_store.delete_genre(genre_key)
    except KeyError:
        return redirect("/genres", error="That genre has no custom definition.")
    return redirect("/genres", flash=f"Deleted custom genre {genre_key}.")


# ── Activity ─────────────────────────────────────────────────────────────────
@app.get("/activity", response_class=HTMLResponse)
async def activity(request: Request):
    limit = 40
    rows = await db.recent_seen(limit=limit)
    return page(request, "activity.html", rows=rows, limit=limit)


# ── Polling / status API ─────────────────────────────────────────────────────
@app.post("/api/refresh")
async def api_refresh(request: Request):
    if rt.RUNTIME.bot is None or not rt.RUNTIME.bot_ready:
        return JSONResponse(
            {"ok": False, "error": "Bot is not connected; cannot poll."}, status_code=409
        )
    if rt.RUNTIME.poll_in_progress:
        return JSONResponse({"ok": False, "error": "Poll already running."}, status_code=409)

    try:
        result = await poller.poll_once(
            rt.RUNTIME.bot, announce=True, trigger="manual"
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    return JSONResponse(
        {
            "ok": True,
            "fetched": result.fetched,
            "new": result.new,
            "announced": result.announced,
            "seeded": result.seeded,
            "errors": result.errors,
        }
    )


@app.get("/api/status")
async def api_status():
    payload = rt.RUNTIME.as_dict()
    payload["seen_count"] = await db.count_seen()
    payload["studios"] = len(config.REGISTRY)
    payload["genres"] = len(config.REGISTRY.all_genres)
    payload["filter_mode"] = config.FILTER_MODE
    return JSONResponse(payload)


@app.get("/api/status/card", response_class=HTMLResponse)
async def api_status_card(request: Request):
    """HTMX partial: the live status strip on the dashboard."""
    payload = rt.RUNTIME.as_dict()
    payload["seen_count"] = await db.count_seen()
    return TEMPLATES.TemplateResponse(
        request, "_status_card.html", {"request": request, "status": payload}
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True, "ready": rt.RUNTIME.ready}


# ── Backups ──────────────────────────────────────────────────────────────────
@app.get("/backups", response_class=HTMLResponse)
async def backups_page(request: Request):
    return page(request, "backups.html", backups=cs.list_backups(limit=50))


@app.post("/backups/restore")
async def backups_restore(request: Request, name: str = Form(...)):
    try:
        cs.restore_backup(name)
    except FileNotFoundError:
        return redirect("/backups", error="Backup not found.")
    return redirect("/backups", flash=f"Restored {name}.")


# ── Entry point ──────────────────────────────────────────────────────────────
def main() -> None:
    if not auth.password_configured():
        print(
            "\n[!] WEBAPP_PASSWORD is not set.\n"
            "    Add it to .env, e.g.  WEBAPP_PASSWORD=change-me\n"
        )
        raise SystemExit(1)

    print(
        f"\n  Marquee dashboard  ->  http://{config.WEBAPP_HOST}:{config.WEBAPP_PORT}\n"
        f"  bot in this process: {'yes' if config.WEBAPP_RUN_BOT else 'no'}\n"
    )
    uvicorn.run(
        app,
        host=config.WEBAPP_HOST,
        port=config.WEBAPP_PORT,
        log_level=config.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
