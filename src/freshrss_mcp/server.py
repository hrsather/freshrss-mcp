import functools
import hmac
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from freshrss_mcp import __version__
from freshrss_mcp.client import READ, STARRED, FreshRSSClient, FreshRSSError
from freshrss_mcp.config import Settings

P = ParamSpec("P")
R = TypeVar("R")

MAX_IDS = 100
MAX_CHARS = 20_000
MAX_SCAN = 500
MAX_OPML = 1_000_000

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
WRITE_EXTERNAL = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)


def _surface(fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except FreshRSSError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _confirm(confirm: bool, action: str) -> None:
    if not confirm:
        raise ToolError(
            f"Refusing to {action} without confirmation. Ask the user, then call again with "
            "confirm=true."
        )


def _ids(ids: list[str]) -> list[str]:
    if not ids:
        raise ToolError("ids must not be empty")
    if len(ids) > MAX_IDS:
        raise ToolError(f"at most {MAX_IDS} ids per call")
    return ids


def _chars(max_chars: int) -> int:
    return max(0, min(max_chars, MAX_CHARS))


class BearerAuth:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            headers = dict(scope["headers"])
            supplied = headers.get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self._expected):
                if scope["type"] == "http":
                    response = PlainTextResponse(
                        "unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
                    )
                    await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


def build_server(settings: Settings, client: FreshRSSClient | None = None) -> MCPServer:
    client = client or FreshRSSClient(settings)
    mcp = MCPServer("freshrss", version=__version__)

    @mcp.tool(annotations=READ_ONLY)
    @_surface
    def freshrss_list_feeds() -> list[dict[str, Any]]:
        """List subscribed feeds with id, title, url, categories and unread count."""
        return client.list_feeds()

    @mcp.tool(annotations=READ_ONLY)
    @_surface
    def freshrss_list_categories() -> list[dict[str, Any]]:
        """List categories (folders) with their unread counts."""
        return client.list_categories()

    @mcp.tool(annotations=READ_ONLY)
    @_surface
    def freshrss_get_unread_counts() -> dict[str, Any]:
        """Unread totals: overall, per feed and per category."""
        return client.get_unread_counts()

    @mcp.tool(annotations=READ_ONLY)
    @_surface
    def freshrss_list_articles(
        stream: str = "unread",
        target: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        since: str | None = None,
        order: str = "newest",
        unread_only: bool = False,
        max_chars: int = 500,
    ) -> dict[str, Any]:
        """List articles. `stream` is unread, all, starred, feed or category; `target` is the
        feed id (e.g. feed/12) or category name for the last two. `limit` is at most 50; pass
        `next_cursor` from the previous result as `cursor` for the next page. `since` is an ISO
        8601 date or datetime; `order` is newest or oldest. `unread_only` filters feed, category
        and starred streams. Text is plain, cut to `max_chars` (see `truncated`); use
        freshrss_get_article for more."""
        return client.list_articles(
            stream, target, limit, cursor, since, order, unread_only, _chars(max_chars)
        )

    @mcp.tool(annotations=READ_ONLY)
    @_surface
    def freshrss_get_article(id: str, max_chars: int = 4000) -> dict[str, Any]:
        """Get one article by its id, with plain text up to `max_chars` (see `truncated`)."""
        return client.get_article(id, _chars(max_chars))

    @mcp.tool(annotations=READ_ONLY)
    @_surface
    def freshrss_search_articles(
        query: str,
        stream: str = "all",
        target: str | None = None,
        unread_only: bool = False,
        max_scan: int = 200,
        limit: int = 20,
        max_chars: int = 500,
    ) -> dict[str, Any]:
        """Case-insensitive text search over title and body. FreshRSS has no server-side
        search, so this scans the newest `max_scan` articles (at most 500) of the stream; the
        result reports `scanned` and `truncated` (true when older articles were not checked)."""
        return client.search_articles(
            query,
            stream,
            target,
            unread_only,
            max(1, min(max_scan, MAX_SCAN)),
            max(1, min(limit, 50)),
            _chars(max_chars),
        )

    @mcp.tool(annotations=READ_ONLY)
    @_surface
    def freshrss_export_opml() -> dict[str, str]:
        """Export all subscriptions as OPML."""
        return client.export_opml()

    if not settings.read_only:

        @mcp.tool(annotations=WRITE)
        @_surface
        def freshrss_mark_read(ids: list[str]) -> dict[str, int]:
            """Mark articles read by id (at most 100)."""
            return {"updated": client.edit_tags(_ids(ids), add=READ)}

        @mcp.tool(annotations=WRITE)
        @_surface
        def freshrss_mark_unread(ids: list[str]) -> dict[str, int]:
            """Mark articles unread by id (at most 100)."""
            return {"updated": client.edit_tags(_ids(ids), remove=READ)}

        @mcp.tool(annotations=WRITE)
        @_surface
        def freshrss_star(ids: list[str]) -> dict[str, int]:
            """Star articles by id (at most 100)."""
            return {"updated": client.edit_tags(_ids(ids), add=STARRED)}

        @mcp.tool(annotations=WRITE)
        @_surface
        def freshrss_unstar(ids: list[str]) -> dict[str, int]:
            """Unstar articles by id (at most 100)."""
            return {"updated": client.edit_tags(_ids(ids), remove=STARRED)}

        @mcp.tool(annotations=WRITE_EXTERNAL)
        @_surface
        def freshrss_subscribe(
            url: str, title: str | None = None, category: str | None = None
        ) -> dict[str, Any]:
            """Subscribe to the feed at `url`, optionally with a title and a category (created
            if missing)."""
            return client.subscribe(url, title, category)

        @mcp.tool(annotations=WRITE)
        @_surface
        def freshrss_edit_feed(
            feed: str, title: str | None = None, category: str | None = None
        ) -> dict[str, str]:
            """Rename a feed and/or move it to `category` (an empty string removes its
            category). `feed` is the feed id, e.g. feed/12."""
            return client.edit_feed(feed, title, category)

        @mcp.tool(annotations=WRITE)
        @_surface
        def freshrss_rename_category(name: str, new_name: str) -> dict[str, str]:
            """Rename a category."""
            return client.rename_category(name, new_name)

        @mcp.tool(annotations=DESTRUCTIVE)
        @_surface
        def freshrss_unsubscribe(feed: str, confirm: bool = False) -> dict[str, str]:
            """Unsubscribe from a feed, deleting its articles. Requires confirm=true."""
            _confirm(confirm, "unsubscribe from the feed")
            return client.unsubscribe(feed)

        @mcp.tool(annotations=DESTRUCTIVE)
        @_surface
        def freshrss_delete_category(name: str, confirm: bool = False) -> dict[str, str]:
            """Delete a category. Requires confirm=true."""
            _confirm(confirm, "delete the category")
            return client.delete_category(name)

        @mcp.tool(annotations=DESTRUCTIVE)
        @_surface
        def freshrss_import_opml(opml: str, confirm: bool = False) -> dict[str, str]:
            """Import subscriptions from OPML text (at most 1 MB). Requires confirm=true."""
            _confirm(confirm, "import OPML")
            if len(opml) > MAX_OPML:
                raise ToolError("OPML is larger than 1 MB")
            return client.import_opml(opml)

        @mcp.tool(annotations=DESTRUCTIVE)
        @_surface
        def freshrss_mark_all_read(
            stream: str = "all", target: str | None = None, confirm: bool = False
        ) -> dict[str, str]:
            """Mark every article in a stream (all, starred, feed or category; `target` as in
            freshrss_list_articles) as read. Requires confirm=true."""
            _confirm(confirm, "mark everything read")
            return client.mark_all_read(stream, target)

    return mcp


def build_sse_app(mcp: MCPServer, token: str, host: str = "127.0.0.1") -> ASGIApp:
    return BearerAuth(mcp.sse_app(host=host), token)
