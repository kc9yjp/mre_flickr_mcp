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

from flickr_api import _load_env
from mcp_tools import _background_refresh, server
from web import main_sse


async def main_stdio():
    from mcp.server.stdio import stdio_server
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

    if MCP_TRANSPORT == "sse":
        await main_sse()
    else:
        await main_stdio()


if __name__ == "__main__":
    asyncio.run(main())
