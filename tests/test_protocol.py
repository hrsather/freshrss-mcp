import os
import socket
import sys
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
import uvicorn
from mcp.client import Client
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters

from freshrss_mcp.config import Settings
from freshrss_mcp.server import build_server, build_sse_app

from .support import BEARER, ITEM


def _env(settings: Settings, **extra: str) -> dict[str, str]:
    return {
        **os.environ,
        "FRESHRSS_URL": settings.url,
        "FRESHRSS_USERNAME": settings.username,
        "FRESHRSS_API_PASSWORD": settings.api_password,
        **extra,
    }


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def sse_url(settings: Settings) -> Iterator[str]:
    port = _free_port()
    app = build_sse_app(build_server(settings), BEARER)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/sse"
    try:
        for _ in range(100):
            try:
                httpx.get(url, timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.1)
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


async def test_stdio(settings: Settings) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "freshrss_mcp"], env=_env(settings)
    )
    async with Client(params) as mcp:
        assert len((await mcp.list_tools()).tools) == 18
        result = await mcp.call_tool("freshrss_list_feeds", {})
        assert [f["title"] for f in result.structured_content["result"]] == ["Alpha", "Beta"]


async def test_stdio_read_only(settings: Settings) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "freshrss_mcp"],
        env=_env(settings, FRESHRSS_READ_ONLY="true"),
    )
    async with Client(params) as mcp:
        assert len((await mcp.list_tools()).tools) == 7


def test_sse_rejects_missing_and_wrong_token(sse_url: str) -> None:
    assert httpx.get(sse_url).status_code == 401
    assert httpx.get(sse_url, headers={"Authorization": "Bearer nope"}).status_code == 401


async def test_sse_with_token(sse_url: str) -> None:
    transport = sse_client(sse_url, headers={"Authorization": f"Bearer {BEARER}"})
    async with Client(transport) as mcp:
        assert len((await mcp.list_tools()).tools) == 18
        item = f"{ITEM}{1:016x}"
        marked = await mcp.call_tool("freshrss_mark_read", {"ids": [item]})
        assert not marked.is_error
        article = await mcp.call_tool("freshrss_get_article", {"id": item})
        assert article.structured_content["read"] is True
