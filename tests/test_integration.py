"""Run against the throwaway FreshRSS from scripts/freshrss-test.sh: start it with `up`, then set
FRESHRSS_TEST_URL to the URL it prints."""

import os
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp.client import Client

from freshrss_mcp.client import FreshRSSClient, FreshRSSError
from freshrss_mcp.config import Settings
from freshrss_mcp.server import build_server

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("FRESHRSS_TEST_URL"), reason="FRESHRSS_TEST_URL not set"),
]

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Integration Feed</title><link>http://example.com/</link><description>Synthetic</description>
<item><title>Kubernetes tips</title><link>http://example.com/1</link><guid>it-1</guid>
<pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate>
<description>&lt;p&gt;Run &lt;b&gt;pods&lt;/b&gt; well.&lt;/p&gt;</description></item>
<item><title>Gardening</title><link>http://example.com/2</link><guid>it-2</guid>
<pubDate>Tue, 02 Jun 2026 10:00:00 GMT</pubDate>
<description>&lt;p&gt;Tomatoes and basil.&lt;/p&gt;</description></item>
<item><title>Election news</title><link>http://example.com/3</link><guid>it-3</guid>
<pubDate>Wed, 03 Jun 2026 10:00:00 GMT</pubDate>
<description>&lt;p&gt;Results are in.&lt;/p&gt;</description></item>
</channel></rss>
"""


class _FeedHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = FEED.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def feed_base() -> Iterator[str]:
    server = ThreadingHTTPServer(("0.0.0.0", 0), _FeedHandler)  # noqa: S104
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://host.docker.internal:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(
        url=os.environ["FRESHRSS_TEST_URL"].rstrip("/"),
        username="admin",
        api_password="apipassword123",
    )


@pytest.fixture
def client(settings: Settings) -> FreshRSSClient:
    return FreshRSSClient(settings)


@pytest.fixture
def feed(client: FreshRSSClient, feed_base: str) -> Iterator[str]:
    url = f"{feed_base}/feed.xml?run={uuid.uuid4().hex}"
    feed_id = client.subscribe(url, "Integration Feed", "IT Category")["feed_id"]
    assert feed_id
    yield feed_id
    try:
        client.unsubscribe(feed_id)
    except FreshRSSError:
        pass


def test_subscribe_lists_feed_with_unread(client: FreshRSSClient, feed: str) -> None:
    listed = next(f for f in client.list_feeds() if f["id"] == feed)
    assert listed["title"] == "Integration Feed"
    assert listed["categories"] == ["IT Category"]
    assert listed["unread"] == 3
    assert "IT Category" in [c["name"] for c in client.list_categories()]
    counts = client.get_unread_counts()
    assert {"id": feed, "title": "Integration Feed", "unread": 3} in counts["feeds"]


def test_articles_ids_text_and_pagination(client: FreshRSSClient, feed: str) -> None:
    page = client.list_articles(stream="feed", target=feed, limit=2)
    assert len(page["articles"]) == 2
    assert page["next_cursor"]
    rest = client.list_articles(stream="feed", target=feed, limit=2, cursor=page["next_cursor"])
    articles = page["articles"] + rest["articles"]
    assert len(articles) == 3
    assert rest["next_cursor"] is None
    assert [a["title"] for a in articles] == ["Election news", "Gardening", "Kubernetes tips"]
    assert all(a["id"].startswith("tag:google.com,2005:reader/item/") for a in articles)
    assert articles[2]["text"] == "Run pods well."
    assert articles[2]["feed_title"] == "Integration Feed"
    again = client.list_articles(stream="feed", target=feed)["articles"]
    assert [a["id"] for a in again] == [a["id"] for a in articles]


def test_get_article_and_search(client: FreshRSSClient, feed: str) -> None:
    found = client.search_articles("KUBERNETES", stream="feed", target=feed)
    assert [a["title"] for a in found["articles"]] == ["Kubernetes tips"]
    assert found["scanned"] == 3
    assert found["truncated"] is False
    article = client.get_article(found["articles"][0]["id"])
    assert article["text"] == "Run pods well."
    assert article["url"] == "http://example.com/1"


def test_read_and_star_roundtrip(client: FreshRSSClient, feed: str) -> None:
    first = client.list_articles(stream="feed", target=feed)["articles"][0]["id"]
    client.edit_tags([first], add="user/-/state/com.google/read")
    assert client.get_article(first)["read"] is True
    unread = client.list_articles(stream="feed", target=feed, unread_only=True)["articles"]
    assert first not in [a["id"] for a in unread]
    client.edit_tags([first], remove="user/-/state/com.google/read")
    assert client.get_article(first)["read"] is False
    client.edit_tags([first], add="user/-/state/com.google/starred")
    assert [a["id"] for a in client.list_articles(stream="starred")["articles"]] == [first]
    client.edit_tags([first], remove="user/-/state/com.google/starred")
    assert client.list_articles(stream="starred")["articles"] == []


def test_edit_feed_and_categories(client: FreshRSSClient, feed: str) -> None:
    client.edit_feed(feed, title="Renamed Feed", category="Moved")
    moved = next(f for f in client.list_feeds() if f["id"] == feed)
    assert (moved["title"], moved["categories"]) == ("Renamed Feed", ["Moved"])
    client.rename_category("Moved", "Moved Again")
    assert next(f for f in client.list_feeds() if f["id"] == feed)["categories"] == ["Moved Again"]
    articles = client.list_articles(stream="category", target="Moved Again")["articles"]
    assert len(articles) == 3
    client.delete_category("Moved Again")
    assert "Moved Again" not in [c["name"] for c in client.list_categories()]


def test_empty_category_moves_the_feed_to_the_default_one(
    client: FreshRSSClient, feed: str
) -> None:
    client.edit_feed(feed, category="")
    assert next(f for f in client.list_feeds() if f["id"] == feed)["categories"] == [
        "Uncategorized"
    ]


def test_category_names_may_contain_slashes(client: FreshRSSClient, feed: str) -> None:
    client.edit_feed(feed, category="Work/Tech")
    assert next(f for f in client.list_feeds() if f["id"] == feed)["categories"] == ["Work/Tech"]
    articles = client.list_articles(stream="category", target="Work/Tech")["articles"]
    assert len(articles) == 3
    client.mark_all_read("category", "Work/Tech")
    assert (
        client.list_articles(stream="category", target="Work/Tech", unread_only=True)["articles"]
        == []
    )


def test_subscribing_twice_returns_the_existing_feed(client: FreshRSSClient, feed: str) -> None:
    url = next(f for f in client.list_feeds() if f["id"] == feed)["url"]
    again = client.subscribe(url)
    assert again == {"subscribed": url, "feed_id": feed, "already_subscribed": True}


def test_mark_all_read(client: FreshRSSClient, feed: str) -> None:
    client.mark_all_read("feed", feed)
    assert client.list_articles(stream="feed", target=feed, unread_only=True)["articles"] == []
    assert next(f for f in client.list_feeds() if f["id"] == feed)["unread"] == 0


def test_unsubscribe(client: FreshRSSClient, feed: str) -> None:
    client.unsubscribe(feed)
    assert feed not in [f["id"] for f in client.list_feeds()]


def test_opml_export_and_import(client: FreshRSSClient, feed: str, feed_base: str) -> None:
    exported = client.export_opml()["opml"]
    assert "<opml" in exported
    assert "Integration Feed" in exported
    url = f"{feed_base}/imported.xml?run={uuid.uuid4().hex}"
    opml = (
        '<?xml version="1.0"?><opml version="2.0"><body>'
        f'<outline text="Imp" xmlUrl="{url}"/></body></opml>'
    )
    client.import_opml(opml)
    imported = next(f for f in client.list_feeds() if f["url"] == url)
    client.unsubscribe(imported["id"])


def test_subscribe_to_unreachable_feed_is_an_actionable_error(client: FreshRSSClient) -> None:
    with pytest.raises(FreshRSSError, match="rejected the request"):
        client.subscribe("http://127.0.0.1:9/nothing.xml")


def test_recovers_from_a_stale_auth_token(client: FreshRSSClient) -> None:
    client.list_feeds()
    client._auth = "stale/token"  # noqa: SLF001
    client._token = "stale"  # noqa: SLF001
    assert client.list_feeds()
    assert (
        client.edit_tags(
            ["tag:google.com,2005:reader/item/0000000000000000"], add="user/-/state/com.google/read"
        )
        == 1
    )


def test_bad_password_is_reported(settings: Settings) -> None:
    bad = FreshRSSClient(
        Settings(url=settings.url, username=settings.username, api_password="nope")
    )
    with pytest.raises(FreshRSSError, match="rejected the credentials"):
        bad.list_feeds()


async def test_mcp_tools_end_to_end(settings: Settings, feed: str) -> None:
    async with Client(build_server(settings)) as mcp:
        listed = await mcp.call_tool("freshrss_list_articles", {"stream": "feed", "target": feed})
        first = listed.structured_content["articles"][0]["id"]
        refused = await mcp.call_tool("freshrss_unsubscribe", {"feed": feed})
        assert refused.is_error
        await mcp.call_tool("freshrss_mark_read", {"ids": [first]})
        article = await mcp.call_tool("freshrss_get_article", {"id": first})
        assert article.structured_content["read"] is True


def test_mark_all_read_on_starred(client: FreshRSSClient, feed: str) -> None:
    first = client.list_articles(stream="feed", target=feed)["articles"][0]["id"]
    client.edit_tags([first], add="user/-/state/com.google/starred")
    client.mark_all_read("starred", None)
    assert client.get_article(first)["read"] is True
    client.edit_tags([first], remove="user/-/state/com.google/starred")
