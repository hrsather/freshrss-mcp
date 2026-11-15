import dataclasses
from datetime import UTC, datetime

import pytest

from freshrss_mcp.client import READ, STARRED, FreshRSSClient, FreshRSSError

from .support import CREDENTIAL, ITEM, OPML, FakeGreader


def _id(number: int) -> str:
    return f"{ITEM}{number:016x}"


def test_list_feeds_and_categories(client: FreshRSSClient) -> None:
    feeds = client.list_feeds()
    assert [(f["id"], f["title"], f["categories"], f["unread"]) for f in feeds] == [
        ("feed/1", "Alpha", ["Tech"], 2),
        ("feed/2", "Beta", ["News"], 2),
    ]
    assert feeds[0]["site_url"] == "http://alpha.example.com/"
    assert client.list_categories() == [
        {"name": "News", "unread": 2},
        {"name": "Tech", "unread": 2},
    ]


def test_unread_counts(client: FreshRSSClient) -> None:
    counts = client.get_unread_counts()
    assert counts["total"] == 4
    assert {"id": "feed/1", "title": "Alpha", "unread": 2} in counts["feeds"]
    assert {"name": "Tech", "unread": 2} in counts["categories"]


def test_list_articles_shape_and_html_stripping(client: FreshRSSClient) -> None:
    page = client.list_articles(stream="feed", target="1", order="oldest")
    first = page["articles"][0]
    assert first["id"] == _id(1)
    assert first["text"] == "Run pods well."
    assert "script" not in first["text"]
    assert first["title"] == "Kubernetes tips"
    assert first["feed_id"] == "feed/1"
    assert first["feed_title"] == "Alpha"
    assert first["categories"] == ["Tech"]
    assert first["published"].startswith("2026-")
    assert first["read"] is False
    assert first["starred"] is False
    assert first["author"] is None
    assert page["next_cursor"] is None


def test_truncation_is_reported(client: FreshRSSClient) -> None:
    long = client.get_article(_id(4), max_chars=50)
    assert long["truncated"] is True
    assert long["text_length"] == 1999
    assert long["text"].endswith("…")
    assert len(long["text"]) <= 51
    full = client.get_article(_id(4), max_chars=5000)
    assert full["truncated"] is False
    assert len(full["text"]) == full["text_length"]


def test_pagination_cursor(client: FreshRSSClient, fake: FakeGreader) -> None:
    fake.add_bulk(6)
    first = client.list_articles(stream="all", limit=4)
    assert len(first["articles"]) == 4
    cursor = first["next_cursor"]
    assert isinstance(cursor, str)
    assert ":" not in cursor
    second = client.list_articles(stream="all", limit=4, cursor=cursor)
    third = client.list_articles(stream="all", limit=4, cursor=second["next_cursor"])
    ids = [a["id"] for page in (first, second, third) for a in page["articles"]]
    assert len(ids) == 10
    assert len(set(ids)) == 10
    assert third["next_cursor"] is None


def test_cursor_is_stable_across_clients(
    settings: object, client: FreshRSSClient, fake: FakeGreader
) -> None:
    fake.add_bulk(5)
    cursor = client.list_articles(stream="all", limit=3)["next_cursor"]
    other = FreshRSSClient(settings)  # type: ignore[arg-type]
    assert len(other.list_articles(stream="all", limit=3, cursor=cursor)["articles"]) == 3


def test_limit_is_clamped(client: FreshRSSClient, fake: FakeGreader) -> None:
    client.list_articles(limit=500)
    client.list_articles(limit=0)
    sizes = [q["n"] for _, p, q in fake.log if "stream/contents" in p]
    assert sizes == [["50"], ["1"]]


def test_unread_filter_follows_read_state(client: FreshRSSClient) -> None:
    client.edit_tags([_id(1)], add=READ)
    unread = client.list_articles(stream="unread")["articles"]
    assert _id(1) not in [a["id"] for a in unread]
    everything = client.list_articles(stream="all")["articles"]
    assert next(a for a in everything if a["id"] == _id(1))["read"] is True
    only_feed_unread = client.list_articles(stream="feed", target="feed/1", unread_only=True)
    assert [a["id"] for a in only_feed_unread["articles"]] == [_id(2)]


def test_category_and_starred_streams(client: FreshRSSClient) -> None:
    news = client.list_articles(stream="category", target="News")["articles"]
    assert {a["feed_id"] for a in news} == {"feed/2"}
    client.edit_tags([_id(3)], add=STARRED)
    starred = client.list_articles(stream="starred")["articles"]
    assert [(a["id"], a["starred"]) for a in starred] == [(_id(3), True)]
    client.edit_tags([_id(3)], remove=STARRED)
    assert client.list_articles(stream="starred")["articles"] == []


def test_since_and_order(client: FreshRSSClient) -> None:
    newest = [a["id"] for a in client.list_articles(stream="all")["articles"]]
    assert newest == [_id(4), _id(3), _id(2), _id(1)]
    oldest = [a["id"] for a in client.list_articles(stream="all", order="oldest")["articles"]]
    assert oldest == newest[::-1]
    cutoff = datetime.fromtimestamp(1_780_000_150, UTC).isoformat()
    aware = client.list_articles(stream="all", since=cutoff)
    assert [a["id"] for a in aware["articles"]] == [_id(4), _id(3)]
    naive = client.list_articles(stream="all", since=cutoff.removesuffix("+00:00"))
    assert naive["articles"] == aware["articles"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"stream": "nope"}, "stream must be one of"),
        ({"stream": "feed"}, "target is required"),
        ({"stream": "category"}, "target is required"),
        ({"cursor": "!!!"}, "invalid cursor"),
        ({"since": "yesterday"}, "cannot parse"),
        ({"order": "sideways"}, "order must be"),
    ],
)
def test_list_articles_validation(
    client: FreshRSSClient, kwargs: dict[str, str], message: str
) -> None:
    with pytest.raises(FreshRSSError, match=message):
        client.list_articles(**kwargs)  # type: ignore[arg-type]


def test_get_article(client: FreshRSSClient) -> None:
    assert client.get_article(_id(2))["title"] == "Gardening"
    with pytest.raises(FreshRSSError, match="article not found"):
        client.get_article(_id(99))


def test_search_matches_body_and_title_case_insensitively(client: FreshRSSClient) -> None:
    by_body = client.search_articles("TOMATOES")
    assert [a["id"] for a in by_body["articles"]] == [_id(2)]
    assert by_body["scanned"] == 4
    assert by_body["truncated"] is False
    assert [a["id"] for a in client.search_articles("election")["articles"]] == [_id(3)]
    assert client.search_articles("absent-term")["articles"] == []


def test_search_never_matches_markup(client: FreshRSSClient) -> None:
    assert client.search_articles("script")["articles"] == []
    assert client.search_articles("<b>")["articles"] == []


def test_search_reports_scan_limit(client: FreshRSSClient, fake: FakeGreader) -> None:
    fake.add_bulk(120)
    capped = client.search_articles("zzz", max_scan=60)
    assert capped == {"articles": [], "scanned": 60, "truncated": True}
    exhaustive = client.search_articles("bulk body", max_scan=500, limit=500)
    assert exhaustive["scanned"] == 124
    assert len(exhaustive["articles"]) == 120
    assert exhaustive["truncated"] is False


def test_search_stops_at_limit(client: FreshRSSClient, fake: FakeGreader) -> None:
    fake.add_bulk(30)
    result = client.search_articles("bulk body", limit=5)
    assert len(result["articles"]) == 5
    assert result["truncated"] is True


def test_search_scoped_to_stream(client: FreshRSSClient) -> None:
    assert client.search_articles("election", stream="feed", target="1")["articles"] == []
    assert len(client.search_articles("election", stream="feed", target="2")["articles"]) == 1


def test_edit_tags_sends_every_id(client: FreshRSSClient, fake: FakeGreader) -> None:
    assert client.edit_tags([_id(1), _id(2)], add=READ) == 2
    assert fake.log[-1][2]["i"] == [_id(1), _id(2)]
    assert client.edit_tags([_id(1)], remove=READ) == 1
    states = {a["id"]: a["read"] for a in client.list_articles(stream="all")["articles"]}
    assert states[_id(1)] is False
    assert states[_id(2)] is True


def test_subscribe_edit_unsubscribe(client: FreshRSSClient) -> None:
    result = client.subscribe("http://feeds.example.com/new.xml", "New", "Fresh")
    assert result == {"subscribed": "http://feeds.example.com/new.xml", "feed_id": "feed/3"}
    assert client.edit_feed("3", title="Renamed", category="Moved") == {"edited": "feed/3"}
    new = next(f for f in client.list_feeds() if f["id"] == "feed/3")
    assert (new["title"], new["categories"]) == ("Renamed", ["Moved"])
    client.edit_feed("feed/3", category="")
    assert next(f for f in client.list_feeds() if f["id"] == "feed/3")["categories"] == []
    assert client.unsubscribe("feed/3") == {"unsubscribed": "feed/3"}
    assert "feed/3" not in [f["id"] for f in client.list_feeds()]


def test_edit_feed_unknown(client: FreshRSSClient) -> None:
    with pytest.raises(FreshRSSError, match="feed not found: feed/99"):
        client.edit_feed("feed/99", category="X")


def test_subscribe_rejected_by_freshrss(client: FreshRSSClient) -> None:
    with pytest.raises(FreshRSSError, match="INTERNAL_HOST_ALLOWLIST"):
        client.subscribe("http://private.example.com/feed.xml")


def test_rename_and_delete_category(client: FreshRSSClient) -> None:
    assert client.rename_category("Tech", "Code") == {"renamed": "Tech", "to": "Code"}
    assert "Code" in [c["name"] for c in client.list_categories()]
    assert client.delete_category("Code") == {"deleted": "Code"}
    assert "Code" not in [c["name"] for c in client.list_categories()]


def test_mark_all_read(client: FreshRSSClient) -> None:
    result = client.mark_all_read("feed", "1")
    assert result == {"marked_read": "feed/1"}
    assert [a["feed_id"] for a in client.list_articles(stream="unread")["articles"]] == [
        "feed/2"
    ] * 2
    client.mark_all_read("all", None)
    assert client.list_articles(stream="unread")["articles"] == []


def test_mark_all_read_cuts_off_at_the_newest_article_not_the_clock(
    client: FreshRSSClient, fake: FakeGreader
) -> None:
    client.mark_all_read("feed", "1")
    assert fake.log[-1][2]["ts"] == ["2"]


def test_mark_all_read_on_an_empty_stream_sends_nothing(
    client: FreshRSSClient, fake: FakeGreader
) -> None:
    fake.items = []
    result = client.mark_all_read("all", None)
    assert result == {"marked_read": "user/-/state/com.google/reading-list"}
    assert "/reader/api/0/mark-all-as-read" not in [p for _, p, _ in fake.log]


def test_opml_roundtrip(client: FreshRSSClient, fake: FakeGreader) -> None:
    assert "<opml>" in client.export_opml()["opml"]
    assert client.import_opml(OPML) == {"imported": "ok"}
    assert fake.opml_imported == OPML


def test_auth_is_cached_and_token_fetched_once(client: FreshRSSClient, fake: FakeGreader) -> None:
    client.list_feeds()
    client.list_categories()
    client.edit_tags([_id(1)], add=READ)
    client.edit_tags([_id(2)], add=READ)
    assert fake.logins == 1
    assert fake.tokens == 1


def test_reauth_after_auth_token_expires(client: FreshRSSClient, fake: FakeGreader) -> None:
    client.list_feeds()
    fake.expire_auth()
    assert len(client.list_feeds()) == 2
    assert fake.logins == 2


def test_reauth_after_write_token_goes_bad(client: FreshRSSClient, fake: FakeGreader) -> None:
    client.edit_tags([_id(1)], add=READ)
    fake.valid_token = "rotated"
    assert client.edit_tags([_id(2)], add=READ) == 1
    assert fake.logins == 2
    assert fake.tokens == 2


def test_reauth_happens_only_once(client: FreshRSSClient, fake: FakeGreader) -> None:
    client.list_feeds()
    fake.fail_next = [401, 401]
    with pytest.raises(FreshRSSError, match="rejected the credentials"):
        client.list_feeds()


def test_bad_credentials(settings: object, fake: FakeGreader) -> None:
    bad = FreshRSSClient(dataclasses.replace(settings, api_password="wrong"))  # type: ignore[type-var]
    with pytest.raises(FreshRSSError, match="API password") as exc:
        bad.list_feeds()
    assert "wrong" not in str(exc.value)


def test_login_does_not_follow_redirects(client: FreshRSSClient, fake: FakeGreader) -> None:
    fake.redirect_login = True
    with pytest.raises(FreshRSSError, match="redirected the request to https://rss.example.com"):
        client.list_feeds()
    assert [p for _, p, _ in fake.log] == ["/accounts/ClientLogin"]


def test_get_is_retried_on_server_errors(
    client: FreshRSSClient, fake: FakeGreader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("freshrss_mcp.client.time.sleep", lambda _: None)
    client.list_feeds()
    fake.fail_next = [503, 502]
    assert len(client.list_feeds()) == 2


def test_get_gives_up_after_retries(
    client: FreshRSSClient, fake: FakeGreader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("freshrss_mcp.client.time.sleep", lambda _: None)
    client.list_feeds()
    fake.fail_next = [500, 500, 500]
    with pytest.raises(FreshRSSError, match="HTTP 500"):
        client.list_feeds()


def test_post_is_not_retried(client: FreshRSSClient, fake: FakeGreader) -> None:
    client.edit_tags([_id(1)], add=READ)
    fake.fail_next = [500]
    with pytest.raises(FreshRSSError, match="HTTP 500"):
        client.edit_tags([_id(2)], add=READ)
    assert [p for _, p, _ in fake.log].count("/reader/api/0/edit-tag") == 2


def test_not_found_points_at_api_setting(client: FreshRSSClient, fake: FakeGreader) -> None:
    client.list_feeds()
    fake.fail_next = [404]
    with pytest.raises(FreshRSSError, match="Allow API access"):
        client.list_feeds()


def test_unreachable_server_message(settings: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("freshrss_mcp.client.time.sleep", lambda _: None)
    dead = FreshRSSClient(dataclasses.replace(settings, url="http://127.0.0.1:1"))  # type: ignore[type-var]
    with pytest.raises(FreshRSSError, match="cannot reach FreshRSS at http://127.0.0.1:1"):
        dead.list_feeds()


def test_unreachable_after_login(
    client: FreshRSSClient, fake: FakeGreader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("freshrss_mcp.client.time.sleep", lambda _: None)
    client.list_feeds()
    client._http.base_url = "http://127.0.0.1:1/api/greader.php"  # noqa: SLF001
    with pytest.raises(FreshRSSError, match="cannot reach FreshRSS"):
        client.list_feeds()


def test_post_rejected_with_message(client: FreshRSSClient, fake: FakeGreader) -> None:
    client.list_feeds()
    original = fake._handle  # noqa: SLF001
    fake._handle = lambda *a, **k: ("200 OK", "Error: nope", [])  # type: ignore[method-assign]  # noqa: SLF001
    try:
        with pytest.raises(FreshRSSError, match="rejected the request: Error: nope"):
            client.rename_category("a", "b")
    finally:
        fake._handle = original  # type: ignore[method-assign]  # noqa: SLF001


def test_non_json_response(client: FreshRSSClient, fake: FakeGreader) -> None:
    client.list_feeds()
    fake._handle = lambda *a, **k: ("200 OK", "<html>", [])  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(FreshRSSError, match="non-JSON"):
        client.list_feeds()


def test_login_without_token(client: FreshRSSClient, fake: FakeGreader) -> None:
    fake._login = lambda form: ("200 OK", "nothing useful", [])  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(FreshRSSError, match="did not return a token"):
        client.list_feeds()


def test_secrets_never_appear_in_requests_after_login(
    client: FreshRSSClient, fake: FakeGreader
) -> None:
    client.list_feeds()
    for _, path, params in fake.log:
        if path != "/accounts/ClientLogin":
            assert CREDENTIAL not in str(params)
