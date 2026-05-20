
import asyncio
import json
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.testclient import TestClient
from unittest.mock import MagicMock

def _make_app():
    fake_server = MagicMock()
    async def fake_run(*args, **kwargs):
        pass
    fake_server.run = fake_run
    fake_server.create_initialization_options = MagicMock(return_value={})

    sse = SseServerTransport("/messages/")

    class _SSEHandler:
        async def __call__(self, scope, receive, send):
            async with sse.connect_sse(scope, receive, send) as streams:
                await fake_server.run(streams[0], streams[1], fake_server.create_initialization_options())

    return Starlette(
        routes=[
            Route("/sse", endpoint=_SSEHandler()),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )

def test_hang():
    app = _make_app()
    print("Starting test...")
    with TestClient(app) as client:
        print("Opening stream...")
        with client.stream("GET", "/sse") as resp:
            print(f"Got response: {resp.status_code}")
            assert resp.status_code == 200
        print("Stream closed.")
    print("Test finished.")

if __name__ == "__main__":
    test_hang()
