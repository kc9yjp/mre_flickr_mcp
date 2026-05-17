"""Web UI, OAuth flow, and SSE/uvicorn server setup."""

import asyncio
import collections
import html
import json
import logging
import os
import urllib.parse
from datetime import datetime

import requests
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from db import DB_FILE, db
from flickr_api import CREDENTIALS_FILE, _load_credentials, _load_env, _oauth_params, _sign
from mcp_tools import SYNC_SCRIPT, _background_refresh, _run_sync_script, _sync_lock, server

MCP_API_KEY = os.environ.get("MCP_API_KEY", "")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

_pending_oauth: dict = {}

_FLICKR_REQUEST_TOKEN_URL = "https://www.flickr.com/services/oauth/request_token"
_FLICKR_ACCESS_TOKEN_URL  = "https://www.flickr.com/services/oauth/access_token"
_FLICKR_AUTHORIZE_URL     = "https://www.flickr.com/services/oauth/authorize"

_SITE_TITLE = "Mr E Flickr MCP"
_GITHUB_URL = "https://github.com/kc9yjp/mre_flickr_mcp"
_FLICKR_URL = "https://www.flickr.com/photos/ejwettstein/"
_LOG_DIR = os.environ.get("FLICKR_LOG_DIR", os.path.join(os.getcwd(), "logs"))
_LOG_FILE = os.path.join(_LOG_DIR, "flickr_mcp.log")

_WEB_CSS = """
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: #f5f5f5; color: #222; }
nav { background: #0063dc; color: #fff; padding: 12px 24px; display: flex; gap: 20px; align-items: center; }
nav a { color: #fff; text-decoration: none; font-weight: 500; }
nav a:hover { text-decoration: underline; }
nav .title { font-weight: 700; margin-right: auto; }
main { max-width: 860px; margin: 32px auto; padding: 0 16px; }
h1 { font-size: 1.5rem; margin-bottom: 20px; }
h2 { font-size: 1.1rem; margin: 24px 0 10px; color: #555; }
.card { background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.1); padding: 20px 24px; margin-bottom: 16px; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
.stat { text-align: center; padding: 12px; background: #f0f4ff; border-radius: 6px; }
.stat .num { font-size: 1.8rem; font-weight: 700; color: #0063dc; }
.stat .lbl { font-size: .75rem; color: #666; margin-top: 2px; }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
th { text-align: left; padding: 6px 10px; background: #f0f0f0; border-bottom: 2px solid #ddd; }
td { padding: 6px 10px; border-bottom: 1px solid #eee; }
.btn { display: inline-block; padding: 8px 18px; background: #0063dc; color: #fff; border: none;
       border-radius: 5px; cursor: pointer; font-size: .9rem; text-decoration: none; }
.btn:hover { background: #0052b4; }
.btn-secondary { background: #6c757d; }
.btn-secondary:hover { background: #5a6268; }
.tag { display: inline-block; background: #e8f0ff; color: #0040a0; padding: 2px 8px;
       border-radius: 12px; font-size: .8rem; margin: 2px; }
.alert { padding: 12px 18px; border-radius: 6px; margin-bottom: 16px; }
.alert-ok  { background: #d4edda; color: #155724; }
.alert-err { background: #f8d7da; color: #721c24; }
.alert-info { background: #d1ecf1; color: #0c5460; }
footer { text-align: center; padding: 24px 16px; color: #888; font-size: .8rem; border-top: 1px solid #e0e0e0; margin-top: 40px; }
footer a { color: #888; }
.cmd-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-bottom: 8px; }
.cmd-card { background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.1); padding: 14px 16px; }
.cmd-card.needs-url { border-left: 3px solid #f0ad4e; }
.cmd-card.autonomous { border-left: 3px solid #5cb85c; }
.cmd-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.cmd-text { font-family: monospace; font-size: .9rem; font-weight: 600; color: #0040a0; flex: 1; }
.copy-btn { padding: 3px 10px; font-size: .75rem; background: #e8f0ff; color: #0040a0; border: 1px solid #b8cff8;
            border-radius: 4px; cursor: pointer; white-space: nowrap; }
.copy-btn:hover { background: #d0e2ff; }
.copy-btn.copied { background: #d4edda; color: #155724; border-color: #c3e6cb; }
.cmd-desc { font-size: .83rem; color: #444; margin-bottom: 4px; }
.cmd-hint { font-size: .75rem; color: #888; margin-top: 4px; }
.cmd-hint code { font-size: .75rem; background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }
</style>
<script>
function copyCmd(btn, text) {
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1500);
  });
}
</script>
"""


def _html_page(title: str, body: str, logged_in: bool | None = None) -> str:
    import datetime as _dt
    year = _dt.date.today().year
    if logged_in is True:
        nav_links = """
  <a href="/stats">Stats</a>
  <a href="/sync">Sync</a>
  <a href="/logs">Logs</a>
  <a href="/setup">Setup</a>
  <form method="POST" action="/logout" style="margin:0"><button type="submit" style="background:none;border:none;color:#fff;font-weight:500;cursor:pointer;padding:0;font-size:1rem">Logout</button></form>"""
    elif logged_in is False:
        nav_links = """
  <a href="/logs">Logs</a>"""
    else:
        nav_links = """
  <a href="/stats">Stats</a>
  <a href="/sync">Sync</a>
  <a href="/logs">Logs</a>
  <a href="/setup">Setup</a>"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — {_SITE_TITLE}</title>{_WEB_CSS}</head>
<body>
<nav>
  <a href="/" class="title">{_SITE_TITLE}</a>{nav_links}
</nav>
<main>{body}</main>
<footer>
    &copy; {year} Eric Wettstein &mdash; <a href="{_GITHUB_URL}" target="_blank">GitHub</a> &mdash; <a href="{_FLICKR_URL}" target="_blank">Flickr</a>
</footer>
</body></html>"""


# --- Route handlers ---

async def route_root(request: Request):
    msg = request.query_params.get("msg", "")
    logged_in = os.path.exists(CREDENTIALS_FILE)
    username = ""
    if logged_in:
        try:
            creds = _load_credentials()
            username = creds.get("username") or creds.get("user_nsid", "")
        except Exception:
            logged_in = False

    total_photos = 0
    last_sync = None
    db_ok = False
    try:
        conn = db()
        row = conn.execute("SELECT COUNT(*) FROM photos").fetchone()
        total_photos = row[0] if row else 0
        sync_row = conn.execute(
            "SELECT MAX(synced_at) FROM sync_log WHERE type = 'photos'"
        ).fetchone()
        if sync_row and sync_row[0]:
            last_sync = datetime.fromtimestamp(sync_row[0]).strftime("%Y-%m-%d %H:%M")
        conn.close()
        db_ok = True
    except Exception:
        pass

    if not logged_in:
        body = f"""<h1>{_SITE_TITLE}</h1>
        <div class="card" style="text-align:center;max-width:400px;margin:40px auto">
          <div style="font-size:3rem">&#128273;</div>
          <h2 style="margin:12px 0 8px">Connect to Flickr</h2>
          <p style="color:#555;margin-bottom:20px">Login with your Flickr account to get started.</p>
          <a href="/login" class="btn">Login with Flickr &rarr;</a>
        </div>"""
        return HTMLResponse(_html_page("Home", body, logged_in=False))

    syncing = _sync_lock.locked()
    if msg == "ok":
        sync_note = ' Syncing your library in the background &mdash; check the <a href="/sync">Sync</a> page for progress.' if syncing else ""
        status_html = f'<div class="alert alert-ok" style="margin-bottom:20px">Welcome, <strong>{username}</strong>! You\'re connected to Flickr.{sync_note}</div>'
    else:
        status_html = f'<div class="alert alert-ok" style="margin-bottom:20px">Logged in as <strong>{username}</strong></div>'

    cards = f"""
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px">
      <a href="/stats" style="text-decoration:none">
        <div class="card" style="text-align:center;cursor:pointer">
          <div style="font-size:2rem">&#128202;</div>
          <div style="font-weight:600;margin:8px 0 4px">Stats</div>
          <div style="color:#555;font-size:.85rem">{"Photos: " + f"{total_photos:,}" if db_ok else "No database yet"}</div>
        </div>
      </a>
      <a href="/sync" style="text-decoration:none">
        <div class="card" style="text-align:center;cursor:pointer">
          <div style="font-size:2rem">&#128260;</div>
          <div style="font-weight:600;margin:8px 0 4px">Sync</div>
          <div style="color:#555;font-size:.85rem">{"Last: " + last_sync if last_sync else "Never synced"}</div>
        </div>
      </a>
      <a href="/setup" style="text-decoration:none">
        <div class="card" style="text-align:center;cursor:pointer">
          <div style="font-size:2rem">&#9881;&#65039;</div>
          <div style="font-weight:600;margin:8px 0 4px">Setup</div>
          <div style="color:#555;font-size:.85rem">MCP client config</div>
        </div>
      </a>
    </div>"""

    def _cmd(slug, desc, hint=None, needs_url=False, autonomous=False):
        cls = "cmd-card needs-url" if needs_url else ("cmd-card autonomous" if autonomous else "cmd-card")
        hint_html = f'<div class="cmd-hint">{hint}</div>' if hint else ""
        return f"""<div class="{cls}">
          <div class="cmd-row">
            <span class="cmd-text">/{slug}</span>
            <button class="copy-btn" onclick="copyCmd(this, '/{slug}')">Copy</button>
          </div>
          <div class="cmd-desc">{desc}</div>
          {hint_html}
        </div>"""

    commands = (
        _cmd("flickr-photo",    "Suggest title, description, tags &amp; groups for a photo",
             hint='Reads current Safari tab &mdash; or paste a URL: <code>/flickr-photo &lt;url&gt;</code>', needs_url=True),
        _cmd("flickr-fave",     "Fave a photo immediately, then suggest a comment to post",
             hint='Reads current Safari tab &mdash; or paste a URL: <code>/flickr-fave &lt;url&gt;</code>', needs_url=True),
        _cmd("flickr-boost",    "Add qualifying photos to threshold groups (views &amp; faves)",
             hint='Works from Safari tab &mdash; or paste a URL: <code>/flickr-boost &lt;url&gt;</code>', needs_url=True),
        _cmd("flickr-album",    "Add the current photo to an album",
             hint='Reads current Safari tab &mdash; or paste a URL: <code>/flickr-album &lt;url&gt;</code>', needs_url=True),
        _cmd("flickr-hide",     "Find weak photos, review visually, make private or update &amp; keep",
             autonomous=True),
        _cmd("flickr-contacts", "Review contacts as unfollow candidates one at a time",
             autonomous=True),
        _cmd("flickr-sync",     "Run all syncs: photos, contacts, groups, albums",
             autonomous=True),
    )

    cmd_legend = '<p style="font-size:.75rem;color:#888;margin-bottom:10px"><span style="display:inline-block;width:10px;height:10px;background:#f0ad4e;border-radius:2px;margin-right:4px"></span>needs a photo &nbsp; <span style="display:inline-block;width:10px;height:10px;background:#5cb85c;border-radius:2px;margin-right:4px"></span>runs on its own</p>'
    cmd_section = f'<h2>Quick Commands</h2>{cmd_legend}<div class="cmd-grid">{"".join(commands)}</div>'

    body = f"<h1>{_SITE_TITLE}</h1>{status_html}{cards}{cmd_section}"
    return HTMLResponse(_html_page("Home", body, logged_in=True))


async def route_login(request: Request):
    msg = request.query_params.get("msg", "")
    logged_in = os.path.exists(CREDENTIALS_FILE)

    if logged_in and msg not in ("ok", "err"):
        return RedirectResponse("/", status_code=303)

    alert = ""
    if msg == "ok":
        alert = '<div class="alert alert-ok">Login successful! You are now connected to Flickr.</div>'
    elif msg == "err":
        alert = '<div class="alert alert-err">Login failed. Please try again.</div>'

    body = f"""
    <h1>Connect to Flickr</h1>
    {alert}
    <div class="card" style="text-align:center;max-width:400px;margin:40px auto">
      <div style="font-size:3rem">&#128273;</div>
      <p style="color:#555;margin:12px 0 20px">Authorize this app to access your Flickr account.</p>
      <a href="/login/start" class="btn">Login with Flickr &rarr;</a>
    </div>"""
    return HTMLResponse(_html_page("Login", body, logged_in=False))


async def route_login_start(request: Request):
    try:
        api_key, api_secret = _load_env()
    except Exception as e:
        body = f'<h1>Login</h1><div class="alert alert-err">Config error: {e}</div>'
        return HTMLResponse(_html_page("Login", body), status_code=500)

    callback_url = str(request.base_url).rstrip("/") + "/oauth/callback"
    params = _oauth_params(api_key, {"oauth_callback": callback_url})
    params["oauth_signature"] = _sign("GET", _FLICKR_REQUEST_TOKEN_URL, params, api_secret)

    try:
        resp = requests.get(_FLICKR_REQUEST_TOKEN_URL, params=params, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        body = f'<h1>Login</h1><div class="alert alert-err">Failed to get request token: {e}</div>'
        return HTMLResponse(_html_page("Login", body), status_code=500)

    token_data = dict(urllib.parse.parse_qsl(resp.text))
    oauth_token = token_data.get("oauth_token")
    oauth_token_secret = token_data.get("oauth_token_secret")

    if not oauth_token:
        body = f'<h1>Login</h1><div class="alert alert-err">Flickr returned no token: {resp.text[:200]}</div>'
        return HTMLResponse(_html_page("Login", body), status_code=500)

    _pending_oauth[oauth_token] = oauth_token_secret
    authorize_url = f"{_FLICKR_AUTHORIZE_URL}?oauth_token={oauth_token}&perms=write"
    return RedirectResponse(authorize_url)


async def route_oauth_callback(request: Request):
    oauth_token    = request.query_params.get("oauth_token", "")
    oauth_verifier = request.query_params.get("oauth_verifier", "")

    if not oauth_token or not oauth_verifier:
        return RedirectResponse("/login?msg=err")

    token_secret = _pending_oauth.pop(oauth_token, None)
    if token_secret is None:
        return RedirectResponse("/login?msg=err")

    try:
        api_key, api_secret = _load_env()
    except Exception:
        return RedirectResponse("/login?msg=err")

    params = _oauth_params(api_key, {
        "oauth_token":    oauth_token,
        "oauth_verifier": oauth_verifier,
    })
    params["oauth_signature"] = _sign("POST", _FLICKR_ACCESS_TOKEN_URL, params, api_secret, token_secret)

    try:
        resp = requests.post(_FLICKR_ACCESS_TOKEN_URL, data=params, timeout=15)
        resp.raise_for_status()
    except Exception:
        logging.exception("OAuth access token exchange failed")
        return RedirectResponse("/login?msg=err")

    token_data = dict(urllib.parse.parse_qsl(resp.text))
    access_token        = token_data.get("oauth_token")
    access_token_secret = token_data.get("oauth_token_secret")
    user_nsid           = token_data.get("user_nsid", "")
    username            = token_data.get("username", "")
    fullname            = token_data.get("fullname", "")

    if not access_token:
        logging.error("No access token in Flickr response: %s", resp.text[:200])
        return RedirectResponse("/login?msg=err")

    creds = {
        "oauth_token":        access_token,
        "oauth_token_secret": access_token_secret,
        "user_nsid":          user_nsid,
        "username":           username,
        "fullname":           fullname,
    }

    os.makedirs(os.path.dirname(CREDENTIALS_FILE), exist_ok=True)
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(creds, f, indent=2)

    logging.info("OAuth login complete for user %s (%s)", username, user_nsid)

    scripts_dir = os.path.dirname(SYNC_SCRIPT)

    async def _post_login_sync():
        async with _sync_lock:
            await _run_sync_script(SYNC_SCRIPT, "photos", extra_args=["--create"])
            await _run_sync_script(os.path.join(scripts_dir, "sync_contacts.py"), "contacts")
            await _run_sync_script(os.path.join(scripts_dir, "sync_groups.py"),   "groups")
            await _run_sync_script(os.path.join(scripts_dir, "sync_albums.py"),   "albums")

    asyncio.create_task(_post_login_sync())
    return RedirectResponse("/?msg=ok", status_code=303)


async def route_logout(request: Request):
    if os.path.exists(CREDENTIALS_FILE):
        os.remove(CREDENTIALS_FILE)
    return RedirectResponse("/", status_code=303)


async def route_stats(request: Request):
    try:
        conn = db()
    except FileNotFoundError:
        body = """<h1>Stats</h1>
        <div class="alert alert-info">No database yet. Run a sync first.</div>
        <p><a href="/sync" class="btn">Go to Sync</a></p>"""
        return HTMLResponse(_html_page("Stats", body))

    try:
        stats = conn.execute("""
            SELECT COUNT(*) AS total_photos,
                   SUM(CASE WHEN is_public = 1 THEN 1 ELSE 0 END) AS public_photos,
                   SUM(CASE WHEN is_public = 0 THEN 1 ELSE 0 END) AS private_photos,
                   SUM(views) AS total_views,
                   MIN(date_taken) AS earliest,
                   MAX(date_taken) AS latest
            FROM photos
        """).fetchone()

        tag_rows = conn.execute(
            "SELECT tags FROM photos WHERE tags != '' AND tags IS NOT NULL"
        ).fetchall()
        group_count   = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        album_count   = conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        contact_count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]

        sync_rows = conn.execute(
            "SELECT type, MAX(synced_at) AS last FROM sync_log GROUP BY type"
        ).fetchall()
    finally:
        conn.close()

    counts = {}
    for row in tag_rows:
        for tag in (row[0] or "").split():
            counts[tag] = counts.get(tag, 0) + 1
    top_tags = sorted(counts.items(), key=lambda x: -x[1])[:20]

    def _ts(ts):
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"

    sync_map = {r["type"]: r["last"] for r in sync_rows}

    stat_grid = f"""
    <div class="stat-grid">
      <div class="stat"><div class="num">{stats['total_photos'] or 0:,}</div><div class="lbl">Photos</div></div>
      <div class="stat"><div class="num">{stats['public_photos'] or 0:,}</div><div class="lbl">Public</div></div>
      <div class="stat"><div class="num">{stats['private_photos'] or 0:,}</div><div class="lbl">Private</div></div>
      <div class="stat"><div class="num">{stats['total_views'] or 0:,}</div><div class="lbl">Total Views</div></div>
      <div class="stat"><div class="num">{album_count:,}</div><div class="lbl">Albums</div></div>
      <div class="stat"><div class="num">{group_count:,}</div><div class="lbl">Groups</div></div>
      <div class="stat"><div class="num">{contact_count:,}</div><div class="lbl">Contacts</div></div>
    </div>"""

    date_range = f"{stats['earliest'] or '?'} &rarr; {stats['latest'] or '?'}"
    tag_html = " ".join(f'<span class="tag">{t} ({c})</span>' for t, c in top_tags)
    sync_html = "".join(
        f"<tr><td>{stype}</td><td>{_ts(stime)}</td></tr>"
        for stype, stime in sync_map.items()
    ) or "<tr><td colspan=2>No syncs recorded</td></tr>"

    body = f"""
    <h1>Collection Stats</h1>
    <div class="card">{stat_grid}
      <p style="margin-top:14px;color:#555;font-size:.9rem">Date range: {date_range}</p>
    </div>
    <h2>Top Tags</h2>
    <div class="card">{tag_html or '<em>No tags</em>'}</div>
    <h2>Last Sync</h2>
    <div class="card">
      <table><thead><tr><th>Type</th><th>Last run</th></tr></thead>
      <tbody>{sync_html}</tbody></table>
      <p style="margin-top:14px"><a href="/sync" class="btn btn-secondary">Go to Sync</a></p>
    </div>"""
    return HTMLResponse(_html_page("Stats", body))


async def route_sync_page(request: Request):
    running = _sync_lock.locked()

    sync_rows = []
    try:
        conn = db()
        sync_rows = conn.execute(
            "SELECT type, MAX(synced_at) AS last FROM sync_log GROUP BY type"
        ).fetchall()
        conn.close()
    except Exception:
        pass

    def _ts(ts):
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"

    sync_html = "".join(
        f"<tr><td>{r['type']}</td><td>{_ts(r['last'])}</td></tr>"
        for r in sync_rows
    ) or "<tr><td colspan=2>No syncs recorded yet</td></tr>"

    running_badge = '<div class="alert alert-info">A sync is currently running&hellip;</div>' if running else ""

    buttons = ""
    for stype in ("photos", "contacts", "groups", "albums", "all"):
        buttons += f"""<form method="POST" action="/sync/{stype}" style="display:inline">
          <button class="btn" style="margin:4px" {"disabled" if running else ""} type="submit">{stype.title()}</button>
        </form>"""

    body = f"""
    <h1>Sync</h1>
    {running_badge}
    <div class="card">
      <h2 style="margin-top:0">Last sync times</h2>
      <table><thead><tr><th>Type</th><th>Last run</th></tr></thead>
      <tbody>{sync_html}</tbody></table>
    </div>
    <div class="card">
      <h2 style="margin-top:0">Trigger sync</h2>
      <p style="margin-bottom:12px;color:#555;font-size:.9rem">Syncs run in the background. Refresh this page to see updated times.</p>
      {buttons}
    </div>"""
    return HTMLResponse(_html_page("Sync", body))


async def route_sync_trigger(request: Request):
    sync_type = request.path_params["type"]
    scripts_dir = os.path.dirname(SYNC_SCRIPT)

    script_map = {
        "photos":   SYNC_SCRIPT,
        "contacts": os.path.join(scripts_dir, "sync_contacts.py"),
        "groups":   os.path.join(scripts_dir, "sync_groups.py"),
        "albums":   os.path.join(scripts_dir, "sync_albums.py"),
    }

    if sync_type not in script_map and sync_type != "all":
        return RedirectResponse("/sync", status_code=303)

    if _sync_lock.locked():
        return RedirectResponse("/sync", status_code=303)

    async def _run():
        async with _sync_lock:
            if sync_type == "all":
                for label, path in script_map.items():
                    await _run_sync_script(path, label)
            else:
                await _run_sync_script(script_map[sync_type], sync_type)

    asyncio.create_task(_run())
    return RedirectResponse("/sync", status_code=303)


async def route_setup(request: Request):
    base = str(request.base_url).rstrip("/")
    sse_url = f"{base}/sse"
    key_hint = MCP_API_KEY[:4] + "…" if MCP_API_KEY else "(none configured)"
    auth_line = f'\n      "headers": {{"Authorization": "Bearer {key_hint}"}}' if MCP_API_KEY else ""
    config_json = (
        "{\n"
        '  "mcpServers": {\n'
        '    "flickr": {\n'
        '      "type": "sse",\n'
        f'      "url": "{sse_url}"{("," + auth_line) if MCP_API_KEY else ""}\n'
        "    }\n"
        "  }\n"
        "}"
    )
    body = f"""
    <h1>Setup</h1>
    <div class="card">
      <h2 style="margin-top:0">Connect Claude Code</h2>
      <p style="margin-bottom:12px;color:#555;font-size:.9rem">
        Add this to your <code>.mcp.json</code> (project root or <code>~/.claude/mcp.json</code>):
      </p>
      <pre style="background:#f0f0f0;padding:14px;border-radius:6px;font-size:.85rem;overflow-x:auto">{config_json}</pre>
      {"<p style='margin-top:10px;color:#555;font-size:.85rem'>Replace the key hint with your full <code>MCP_API_KEY</code> value from <code>.env</code>.</p>" if MCP_API_KEY else ""}
    </div>
    <div class="card">
      <h2 style="margin-top:0">Endpoints</h2>
      <table>
        <thead><tr><th>Path</th><th>Purpose</th></tr></thead>
        <tbody>
          <tr><td><code>/sse</code></td><td>MCP SSE endpoint (Claude connects here)</td></tr>
          <tr><td><code>/messages/</code></td><td>MCP POST messages endpoint</td></tr>
            <tr><td><code>/login</code></td><td>Browser-based Flickr OAuth login</td></tr>
          <tr><td><code>/stats</code></td><td>Collection statistics dashboard</td></tr>
          <tr><td><code>/sync</code></td><td>Sync status and trigger page</td></tr>
          <tr><td><code>/logs</code></td><td>View server logs</td></tr>
        </tbody>
      </table>
    </div>"""
    return HTMLResponse(_html_page("Setup", body))


async def route_logs(request: Request):
    max_lines = 250
    tail_lines = []
    if os.path.exists(_LOG_FILE):
        with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            tail_lines = list(collections.deque(f, max_lines))
    if not tail_lines:
        body = """
        <h1>Logs</h1>
        <div class=\"card\">
          <p>No log file found yet.</p>
          <p>Run the server and reload this page to view logs.</p>
        </div>"""
        return HTMLResponse(_html_page("Logs", body))

    log_content = html.escape("".join(tail_lines))
    body = f"""
    <h1>Logs</h1>
    <div class=\"card\">
      <p style=\"margin-bottom:14px;color:#555;font-size:.9rem\">Showing the last {len(tail_lines)} log lines from <code>{_LOG_FILE}</code>.</p>
      <pre style=\"background:#f0f0f0;padding:14px;border-radius:6px;font-size:.8rem;overflow-x:auto;white-space:pre-wrap;word-break:break-word;\">{log_content}</pre>
    </div>"""
    return HTMLResponse(_html_page("Logs", body))


# --- SSE handler and API key middleware ---

class _SSEHandler:
    def __init__(self, sse_transport):
        self._sse = sse_transport

    async def __call__(self, scope, receive, send):
        async with self._sse.connect_sse(scope, receive, send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if MCP_API_KEY:
            path = request.url.path
            if path.startswith("/sse") or path.startswith("/messages"):
                key = request.headers.get("X-API-Key", "")
                if not key:
                    auth = request.headers.get("Authorization", "")
                    if auth.startswith("Bearer "):
                        key = auth[7:]
                if key != MCP_API_KEY:
                    return Response("Unauthorized", status_code=401)
        return await call_next(request)


async def main_sse():
    from mcp.server.sse import SseServerTransport
    import uvicorn

    sse = SseServerTransport("/messages/")
    middleware = [Middleware(ApiKeyMiddleware)] if MCP_API_KEY else []

    app = Starlette(
        middleware=middleware,
        routes=[
            Route("/",               endpoint=route_root),
            Route("/login",          endpoint=route_login),
            Route("/login/start",    endpoint=route_login_start),
            Route("/oauth/callback", endpoint=route_oauth_callback),
            Route("/logout",         endpoint=route_logout, methods=["POST"]),
            Route("/stats",          endpoint=route_stats),
            Route("/sync",           endpoint=route_sync_page),
            Route("/sync/{type}",    endpoint=route_sync_trigger, methods=["POST"]),
            Route("/logs",           endpoint=route_logs),
            Route("/setup",          endpoint=route_setup),
            Route("/sse",            endpoint=_SSEHandler(sse)),
            Mount("/messages/",      app=sse.handle_post_message),
        ],
    )

    config = uvicorn.Config(app, host="0.0.0.0", port=MCP_PORT, log_level="info")
    uv_server = uvicorn.Server(config)

    asyncio.create_task(_background_refresh())
    logging.info("SSE ready on port %d (api_key=%s)", MCP_PORT, "set" if MCP_API_KEY else "none")
    await uv_server.serve()
