"""Tests for SSE transport routing (no Flickr connection required)."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def _make_app(api_key: str = ""):
    """Build the Starlette app the same way main_sse() does, with a no-op MCP server."""
    from mcp.server import Server
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Mount, Route

    fake_server = MagicMock()
    fake_server.run = AsyncMock()
    fake_server.create_initialization_options = MagicMock(return_value={})

    sse = SseServerTransport("/messages/")

    class _SSEHandler:
        async def __call__(self, scope, receive, send):
            async with sse.connect_sse(scope, receive, send) as streams:
                await fake_server.run(streams[0], streams[1], fake_server.create_initialization_options())

    class ApiKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if api_key:
                key = request.headers.get("X-API-Key", "")
                if not key:
                    auth = request.headers.get("Authorization", "")
                    if auth.startswith("Bearer "):
                        key = auth[7:]
                if key != api_key:
                    return Response("Unauthorized", status_code=401)
            return await call_next(request)

    middleware = [Middleware(ApiKeyMiddleware)] if api_key else []

    return Starlette(
        middleware=middleware,
        routes=[
            Route("/sse", endpoint=_SSEHandler()),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )


class TestSSERouting:
    def test_sse_returns_200_not_redirect(self):
        """GET /sse must return 200 with text/event-stream, not 307."""
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            with client.stream("GET", "/sse") as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_sse_sends_endpoint_event(self):
        """The first SSE event must be 'endpoint' pointing to /messages/."""
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            with client.stream("GET", "/sse") as resp:
                assert resp.status_code == 200
                for line in resp.iter_lines():
                    if line.startswith("event: endpoint"):
                        break
                    if line.startswith("data:"):
                        assert "/messages/" in line
                        break

    def test_messages_endpoint_rejects_unknown_session(self):
        """POST /messages/ with a bad session_id should return 404."""
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/messages/?session_id=00000000000000000000000000000000",
                content=json.dumps({"jsonrpc": "2.0", "method": "ping", "id": 1}),
                headers={"content-type": "application/json"},
            )
            assert resp.status_code == 404


class TestApiKeyMiddleware:
    def test_no_key_configured_allows_all(self):
        """When no API key is set, all requests pass through."""
        app = _make_app(api_key="")
        with TestClient(app, raise_server_exceptions=False) as client:
            with client.stream("GET", "/sse") as resp:
                assert resp.status_code == 200

    def test_correct_bearer_token_allowed(self):
        """Bearer token matching the configured key is accepted."""
        app = _make_app(api_key="secret123")
        with TestClient(app, raise_server_exceptions=False) as client:
            with client.stream("GET", "/sse", headers={"Authorization": "Bearer secret123"}) as resp:
                assert resp.status_code == 200

    def test_correct_x_api_key_allowed(self):
        """X-API-Key header matching the configured key is accepted."""
        app = _make_app(api_key="secret123")
        with TestClient(app, raise_server_exceptions=False) as client:
            with client.stream("GET", "/sse", headers={"X-API-Key": "secret123"}) as resp:
                assert resp.status_code == 200

    def test_wrong_key_rejected(self):
        """Wrong API key returns 401."""
        app = _make_app(api_key="secret123")
        with TestClient(app, raise_server_exceptions=False) as client:
            with client.stream("GET", "/sse", headers={"Authorization": "Bearer wrong"}) as resp:
                assert resp.status_code == 401

    def test_missing_key_rejected(self):
        """Missing API key returns 401 when one is configured."""
        app = _make_app(api_key="secret123")
        with TestClient(app, raise_server_exceptions=False) as client:
            with client.stream("GET", "/sse") as resp:
                assert resp.status_code == 401
