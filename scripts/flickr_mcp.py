#!/usr/bin/env python3
"""Flickr MCP server — entry point."""

import asyncio
import logging
import os
import sys

logging.basicConfig(
    stream=sys.stderr,
    level=logging.DEBUG if os.environ.get("MCP_DEBUG") else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "sse")

from flickr_api import _load_env, _resolve_api_key, _all_known_users
from mcp_tools import _background_refresh, server
from web import main_sse


def _migrate_all_user_dbs() -> None:
    """Apply pending schema migrations to all existing user databases at startup.

    Ensures tables added in recent migrations (e.g. pending_group_adds, settings)
    exist before any tool handler tries to use them, even if the user hasn't run
    a sync since upgrading.
    """
    from flickr_sync import _apply_migrations
    from db import db_file, get_db_for_user

    for user in _all_known_users():
        path = db_file(user["username"])
        if not os.path.exists(path):
            continue
        try:
            with get_db_for_user(user["username"]) as conn:
                _apply_migrations(conn)
            logging.debug("Migrations applied for %s", user["username"])
        except Exception:
            logging.exception("Startup migration failed for user %s", user["username"])


async def main_stdio():
    from mcp.server.stdio import stdio_server
    from db import _current_user

    api_key = os.environ.get("MCP_API_KEY", "").strip()
    if api_key:
        user = _resolve_api_key(api_key)
        if not user:
            logging.error("MCP_API_KEY not recognised — log in at http://localhost:8000/login first")
            sys.exit(1)
        _current_user.set(user)
        logging.info("stdio: authenticated as %s (%s)", user["username"], user["nsid"])
    else:
        logging.error("stdio: no MCP_API_KEY set — log in at http://localhost:8000/login first")
        sys.exit(1)

    async with stdio_server() as (read_stream, write_stream):
        asyncio.create_task(_background_refresh())
        logging.info("stdio ready — waiting for MCP client")
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def main():
    logging.info("Flickr MCP server starting (transport=%s)", MCP_TRANSPORT)
    try:
        _load_env()
        logging.info("Env loaded OK")
    except Exception as e:
        logging.error("Startup failed: %s", e)
        sys.exit(1)

    _migrate_all_user_dbs()

    if MCP_TRANSPORT == "sse":
        await main_sse()
    else:
        await main_stdio()


if __name__ == "__main__":
    asyncio.run(main())
