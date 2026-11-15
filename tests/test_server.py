import dataclasses
from typing import Any

import httpx
import pytest
from mcp.client import Client

from freshrss_mcp.client import FreshRSSClient
from freshrss_mcp.config import Settings
from freshrss_mcp.server import BearerAuth, build_server, build_sse_app

from .support import ITEM, OPML, FakeGreader

READ_TOOLS = {
    "freshrss_list_feeds",
    "freshrss_list_categories",
    "freshrss_get_unread_counts",
    "freshrss_list_articles",
    "freshrss_get_article",
    "freshrss_search_articles",
    "freshrss_export_opml",
}
WRITE_TOOLS = {
    "freshrss_mark_read",
    "freshrss_mark_unread",
    "freshrss_star",
    "freshrss_unstar",
    "freshrss_subscribe",
    "freshrss_edit_feed",
    "freshrss_rename_category",
}
DESTRUCTIVE_TOOLS = {
    "freshrss_unsubscribe",
    "freshrss_delete_category",
    "freshrss_import_opml",
    "freshrss_mark_all_read",
}
ITEM_1 = f"{ITEM}{1:016x}"


async def test_tool_set_and_annotations(settings: Settings, client: FreshRSSClient) -> None:
    async with Client(build_server(settings, client)) as mcp:
        tools = {t.name: t for t in (await mcp.list_tools()).tools}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS | DESTRUCTIVE_TOOLS
    for name, tool in tools.items():
        assert tool.annotations is not None, name
        assert tool.annotations.read_only_hint is (name in READ_TOOLS), name
        assert tool.annotations.destructive_hint is (name in DESTRUCTIVE_TOOLS), name


async def test_read_only_hides_write_and_destructive_tools(
    settings: Settings, client: FreshRSSClient
) -> None:
    read_only = dataclasses.replace(settings, read_only=True)
    async with Client(build_server(read_only, client)) as mcp:
        assert {t.name for t in (await mcp.list_tools()).tools} == READ_TOOLS


async def test_read_roundtrip(settings: Settings, client: FreshRSSClient) -> None:
    async with Client(build_server(settings, client)) as mcp:
        feeds = await mcp.call_tool("freshrss_list_feeds", {})
        assert [f["title"] for f in feeds.structured_content["result"]] == ["Alpha", "Beta"]
        listed = await mcp.call_tool("freshrss_list_articles", {"limit": 2, "max_chars": 10})
        assert len(listed.structured_content["articles"]) == 2
        assert listed.structured_content["next_cursor"] is not None
        article = await mcp.call_tool("freshrss_get_article", {"id": ITEM_1})
        assert article.structured_content["text"] == "Run pods well."
        found = await mcp.call_tool("freshrss_search_articles", {"query": "basil"})
        assert [a["title"] for a in found.structured_content["articles"]] == ["Gardening"]
        counts = await mcp.call_tool("freshrss_get_unread_counts", {})
        assert counts.structured_content["total"] == 4
        categories = await mcp.call_tool("freshrss_list_categories", {})
        assert len(categories.structured_content["result"]) == 2
        opml = await mcp.call_tool("freshrss_export_opml", {})
        assert "<opml>" in opml.structured_content["opml"]


async def test_max_chars_is_bounded(settings: Settings, client: FreshRSSClient) -> None:
    async with Client(build_server(settings, client)) as mcp:
        result = await mcp.call_tool("freshrss_list_articles", {"max_chars": -5, "limit": 1})
        assert result.structured_content["articles"][0]["text"] == "…"
        result = await mcp.call_tool("freshrss_get_article", {"id": ITEM_1, "max_chars": 10**9})
        assert result.structured_content["truncated"] is False


async def test_write_roundtrip(settings: Settings, client: FreshRSSClient) -> None:
    async with Client(build_server(settings, client)) as mcp:
        marked = await mcp.call_tool("freshrss_mark_read", {"ids": [ITEM_1]})
        assert marked.structured_content == {"updated": 1}
        article = await mcp.call_tool("freshrss_get_article", {"id": ITEM_1})
        assert article.structured_content["read"] is True
        await mcp.call_tool("freshrss_mark_unread", {"ids": [ITEM_1]})
        await mcp.call_tool("freshrss_star", {"ids": [ITEM_1]})
        starred = await mcp.call_tool("freshrss_list_articles", {"stream": "starred"})
        assert [a["id"] for a in starred.structured_content["articles"]] == [ITEM_1]
        await mcp.call_tool("freshrss_unstar", {"ids": [ITEM_1]})
        subscribed = await mcp.call_tool(
            "freshrss_subscribe", {"url": "http://feeds.example.com/x.xml", "category": "Misc"}
        )
        assert subscribed.structured_content["feed_id"] == "feed/3"
        edited = await mcp.call_tool("freshrss_edit_feed", {"feed": "feed/3", "title": "X"})
        assert edited.structured_content == {"edited": "feed/3"}
        renamed = await mcp.call_tool(
            "freshrss_rename_category", {"name": "Misc", "new_name": "Other"}
        )
        assert not renamed.is_error


@pytest.mark.parametrize("count", [0, 101])
async def test_id_list_bounds(settings: Settings, client: FreshRSSClient, count: int) -> None:
    async with Client(build_server(settings, client)) as mcp:
        result = await mcp.call_tool("freshrss_mark_read", {"ids": ["x"] * count})
        assert result.is_error
        assert "ids" in result.content[0].text


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("freshrss_unsubscribe", {"feed": "feed/1"}),
        ("freshrss_delete_category", {"name": "Tech"}),
        ("freshrss_import_opml", {"opml": OPML}),
        ("freshrss_mark_all_read", {}),
    ],
)
async def test_destructive_tools_refuse_without_confirm(
    settings: Settings, client: FreshRSSClient, fake: FakeGreader, tool: str, args: dict[str, Any]
) -> None:
    async with Client(build_server(settings, client)) as mcp:
        result = await mcp.call_tool(tool, args)
        assert result.is_error
        assert "confirm=true" in result.content[0].text
        refused = await mcp.call_tool(tool, {**args, "confirm": False})
        assert refused.is_error
    assert fake.log == []


async def test_destructive_tools_run_with_confirm(
    settings: Settings, client: FreshRSSClient, fake: FakeGreader
) -> None:
    async with Client(build_server(settings, client)) as mcp:
        done = await mcp.call_tool(
            "freshrss_mark_all_read", {"stream": "feed", "target": "1", "confirm": True}
        )
        assert done.structured_content == {"marked_read": "feed/1"}
        imported = await mcp.call_tool("freshrss_import_opml", {"opml": OPML, "confirm": True})
        assert not imported.is_error
        category = await mcp.call_tool(
            "freshrss_delete_category", {"name": "Tech", "confirm": True}
        )
        assert category.structured_content == {"deleted": "Tech"}
        gone = await mcp.call_tool("freshrss_unsubscribe", {"feed": "feed/2", "confirm": True})
        assert gone.structured_content == {"unsubscribed": "feed/2"}
    assert [s["id"] for s in fake.subscriptions] == ["feed/1"]


async def test_opml_size_limit(settings: Settings, client: FreshRSSClient) -> None:
    async with Client(build_server(settings, client)) as mcp:
        # FreshRSS caps bytes, not characters: this is 524,289 characters but 1,048,578 bytes
        result = await mcp.call_tool(
            "freshrss_import_opml", {"opml": "é" * 524_289, "confirm": True}
        )
        assert result.is_error
        assert "1 MB" in result.content[0].text


async def test_errors_reach_the_model_as_plain_strings(
    settings: Settings, client: FreshRSSClient
) -> None:
    async with Client(build_server(settings, client)) as mcp:
        missing = f"{ITEM}{99:016x}"
        result = await mcp.call_tool("freshrss_get_article", {"id": missing})
        assert result.is_error
        assert f"article not found: {missing}" in result.content[0].text
        assert "Traceback" not in result.content[0].text
        invalid = await mcp.call_tool("freshrss_get_article", {"id": "missing"})
        assert invalid.is_error
        assert "invalid article id" in invalid.content[0].text


async def test_bad_credentials_never_leak(settings: Settings) -> None:
    wrong = dataclasses.replace(settings, api_password="super-secret-wrong")
    async with Client(build_server(wrong)) as mcp:
        result = await mcp.call_tool("freshrss_list_feeds", {})
        assert result.is_error
        assert "super-secret-wrong" not in result.content[0].text


async def _request(app: Any, headers: dict[str, str]) -> int:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return (await http.get("/other", headers=headers)).status_code


async def _inner(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def test_bearer_auth() -> None:
    app = BearerAuth(_inner, "s3cret")
    assert await _request(app, {}) == 401
    assert await _request(app, {"Authorization": "Bearer wrong"}) == 401
    assert await _request(app, {"Authorization": "s3cret"}) == 401
    assert await _request(app, {"Authorization": "Bearer s3cret"}) == 204


async def test_sse_app_requires_token(settings: Settings, client: FreshRSSClient) -> None:
    app = build_sse_app(build_server(settings, client), "s3cret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/sse")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
