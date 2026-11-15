import base64
import binascii
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from freshrss_mcp.config import Settings
from freshrss_mcp.text import clean, strip_html, truncate

READ = "user/-/state/com.google/read"
STARRED = "user/-/state/com.google/starred"
READING_LIST = "user/-/state/com.google/reading-list"
LABEL = "user/-/label/"
API = "/reader/api/0"

MAX_PAGE = 50
GET_RETRIES = 2
STREAMS = ("unread", "all", "starred", "feed", "category")
# Matched on the attribute names, whatever XML namespace prefix they use
OPML_NETWORK_OPTIONS = re.compile(r"CURLOPT_|opmlUrl", re.IGNORECASE)
FEED_ID = re.compile(r"feed/[1-9][0-9]{0,18}")
MAX_HTML = 1_000_000
# FreshRSS reads a decimal-looking id with a leading zero as hex, so those are not accepted
ARTICLE_ID = re.compile(r"tag:google\.com,2005:reader/item/[0-9a-fA-F]{1,16}|[1-9][0-9]{0,19}")

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
# FreshRSS fetches feeds inside these requests
SLOW_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

Form = list[tuple[str, str]]


class FreshRSSError(Exception):
    pass


def valid_article_id(value: str) -> str:
    if not ARTICLE_ID.fullmatch(value):
        raise FreshRSSError(
            f"invalid article id '{value[:80]}': use an id from freshrss_list_articles, "
            "like tag:google.com,2005:reader/item/0006604be806d1d1"
        )
    return value


def _json(response: httpx.Response, key: str | None = None) -> Any:
    try:
        data = response.json()
        return data if key is None else data[key]
    except (ValueError, KeyError, TypeError) as exc:
        raise FreshRSSError(
            "FreshRSS returned an unexpected response (not the expected JSON)"
        ) from exc


def feed_id(feed: str) -> str:
    target = feed if feed.startswith("feed/") else f"feed/{feed}"
    if not FEED_ID.fullmatch(target):
        raise FreshRSSError(
            f"invalid feed id '{feed[:80]}': use an id from freshrss_list_feeds, like feed/12"
        )
    return target


def feed_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FreshRSSError("url must be an http:// or https:// feed URL")
    return url


def label(name: str) -> str:
    return name if name.startswith(LABEL) else f"{LABEL}{name}"


def stream_id(stream: str, target: str | None) -> str:
    if stream not in STREAMS:
        raise FreshRSSError(f"stream must be one of: {', '.join(STREAMS)}")
    if stream == "starred":
        return STARRED
    if stream in {"unread", "all"}:
        return READING_LIST
    if not target:
        raise FreshRSSError(f"target is required when stream is '{stream}'")
    return feed_id(target) if stream == "feed" else label(target)


def _form(fields: Form | None) -> dict[str, list[str]] | None:
    if not fields:
        return None
    grouped: dict[str, list[str]] = {}
    for key, value in fields:
        grouped.setdefault(key, []).append(value)
    return grouped


def _epoch(moment: str) -> int:
    try:
        parsed = datetime.fromisoformat(moment)
    except ValueError as exc:
        raise FreshRSSError(f"cannot parse '{moment}' as an ISO 8601 date or datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def _iso(seconds: Any) -> str | None:
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _encode_cursor(continuation: str) -> str:
    return base64.urlsafe_b64encode(continuation.encode()).decode()


def _decode_cursor(cursor: str) -> str:
    try:
        continuation = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise FreshRSSError("invalid cursor: pass next_cursor from a previous call") from exc
    if _encode_cursor(continuation) != cursor:
        raise FreshRSSError("invalid cursor: pass next_cursor from a previous call")
    return continuation


def _body(item: dict[str, Any]) -> str:
    content = item.get("summary") or item.get("content") or {}
    return strip_html(content.get("content", "")[:MAX_HTML])


def _article(item: dict[str, Any], max_chars: int) -> dict[str, Any]:
    categories: list[str] = item.get("categories", [])
    text = _body(item)
    shown, truncated = truncate(text, max_chars)
    links = item.get("canonical") or item.get("alternate") or [{}]
    origin = item.get("origin", {})
    return {
        "id": item["id"],
        "title": clean(item.get("title", "")),
        "url": links[0].get("href"),
        "feed_id": origin.get("streamId"),
        "feed_title": clean(origin.get("title") or ""),
        "author": clean(item.get("author") or "") or None,
        "published": _iso(item.get("published")),
        "categories": [c[len(LABEL) :] for c in categories if c.startswith(LABEL)],
        "read": READ in categories,
        "starred": STARRED in categories,
        "text": shown,
        "text_length": len(text),
        "truncated": truncated,
    }


class FreshRSSClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        # httpx logs every request URL at INFO
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self._http = httpx.Client(
            base_url=f"{settings.url}/api/greader.php",
            timeout=TIMEOUT,
            follow_redirects=False,
            transport=transport,
        )
        self._auth: str | None = None
        self._token: str | None = None

    def _fail(self, response: httpx.Response) -> FreshRSSError:
        status = response.status_code
        if 300 <= status < 400:
            where = clean(response.headers.get("location", "another URL"))
            return FreshRSSError(
                f"FreshRSS redirected the request to {where}; set FRESHRSS_URL to the final URL"
            )
        if status == 401:
            return FreshRSSError(
                "FreshRSS rejected the credentials: check FRESHRSS_USERNAME and the API password "
                "(not the account password), and that API access is enabled"
            )
        if status == 404:
            return FreshRSSError(
                "FreshRSS API not found: check FRESHRSS_URL and enable 'Allow API access' in "
                "FreshRSS under Settings > Authentication"
            )
        if status == 400:
            return FreshRSSError(
                "FreshRSS rejected the request (HTTP 400): check the arguments; a feed URL must be "
                "reachable, and FreshRSS refuses private addresses unless INTERNAL_HOST_ALLOWLIST "
                "is set"
            )
        return FreshRSSError(f"FreshRSS returned HTTP {status}")

    def _login(self) -> None:
        try:
            response = self._http.post(
                "/accounts/ClientLogin",
                data={"Email": self._settings.username, "Passwd": self._settings.api_password},
            )
        except httpx.HTTPError as exc:
            raise FreshRSSError(
                f"cannot reach FreshRSS at {self._settings.url} ({type(exc).__name__})"
            ) from exc
        if response.status_code != 200:
            raise self._fail(response)
        fields = dict(line.split("=", 1) for line in response.text.splitlines() if "=" in line)
        if "Auth" not in fields:
            raise FreshRSSError("FreshRSS login did not return a token: check the API password")
        self._auth = fields["Auth"]
        self._token = None

    def _write_token(self) -> str:
        if self._token is None:
            self._token = self._request("GET", f"{API}/token").text.strip()
        return self._token

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        data: Form | None = None,
        content: bytes | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        if self._auth is None:
            self._login()
        attempts = 1 + (GET_RETRIES if method == "GET" else 0)
        for attempt in range(attempts):
            try:
                response = self._http.request(
                    method,
                    path,
                    params=params,
                    data=_form(data),
                    content=content,
                    headers={"Authorization": f"GoogleLogin auth={self._auth}"},
                    timeout=timeout or TIMEOUT,
                )
            except httpx.HTTPError as exc:
                if attempt + 1 < attempts:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                applied = "" if method == "GET" else "; the change may still have been applied"
                raise FreshRSSError(
                    f"cannot reach FreshRSS at {self._settings.url} ({type(exc).__name__}){applied}"
                ) from exc
            if response.status_code >= 500 and attempt + 1 < attempts:
                time.sleep(0.2 * (attempt + 1))
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        data: Form | None = None,
        content: bytes | None = None,
        write: bool = False,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        # A bad write token and an expired login are both a 401; a 400 means the request itself
        # was rejected and is never retried.
        for retry in (True, False):
            # subscription/import takes a raw body and never checks the token
            form = (
                [("T", self._write_token()), *(data or [])] if write and content is None else data
            )
            response = self._send(
                method, path, params=params, data=form, content=content, timeout=timeout
            )
            if response.status_code == 401 and retry:
                self._auth = None
                self._token = None
                continue
            if response.status_code != 200:
                raise self._fail(response)
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _get_json(
        self, path: str, params: dict[str, str | int] | None = None, key: str | None = None
    ) -> Any:
        return _json(self._request("GET", path, params={"output": "json", **(params or {})}), key)

    def _post_ok(
        self,
        path: str,
        data: Form,
        content: bytes | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        response = self._request(
            "POST", path, data=data, content=content, write=True, timeout=timeout
        )
        if not response.text.strip().startswith("OK"):
            raise FreshRSSError(f"FreshRSS rejected the request: {clean(response.text, 200)}")

    def _subscriptions(self) -> list[dict[str, Any]]:
        return list(self._get_json(f"{API}/subscription/list", key="subscriptions"))

    def _unread_by_id(self) -> dict[str, int]:
        counts = self._get_json(f"{API}/unread-count", key="unreadcounts")
        return {entry["id"]: int(entry["count"]) for entry in counts}

    def list_feeds(self) -> list[dict[str, Any]]:
        unread = self._unread_by_id()
        return [
            {
                "id": sub["id"],
                "title": clean(sub.get("title", "")),
                "url": sub.get("url"),
                "site_url": sub.get("htmlUrl"),
                "categories": [c["label"] for c in sub.get("categories", [])],
                "unread": unread.get(sub["id"], 0),
            }
            for sub in self._subscriptions()
        ]

    def list_categories(self) -> list[dict[str, Any]]:
        unread = self._unread_by_id()
        tags = self._get_json(f"{API}/tag/list", key="tags")
        return [
            {"name": tag["id"][len(LABEL) :], "unread": unread.get(tag["id"], 0)}
            for tag in tags
            if tag["id"].startswith(LABEL)
        ]

    def get_unread_counts(self) -> dict[str, Any]:
        unread = self._unread_by_id()
        titles = {sub["id"]: clean(sub.get("title", "")) for sub in self._subscriptions()}
        return {
            "total": unread.get(READING_LIST, 0),
            "feeds": [
                {"id": key, "title": titles.get(key, ""), "unread": count}
                for key, count in unread.items()
                if key.startswith("feed/")
            ],
            "categories": [
                {"name": key[len(LABEL) :], "unread": count}
                for key, count in unread.items()
                if key.startswith(LABEL)
            ],
        }

    def _page(
        self,
        stream: str,
        target: str | None,
        limit: int,
        cursor: str | None,
        since: str | None,
        order: str,
        unread_only: bool,
    ) -> dict[str, Any]:
        if order not in {"newest", "oldest"}:
            raise FreshRSSError("order must be 'newest' or 'oldest'")
        params: dict[str, str | int] = {"n": max(1, min(limit, MAX_PAGE))}
        if order == "oldest":
            params["r"] = "o"
        if stream == "unread" or unread_only:
            params["xt"] = READ
        if since:
            params["ot"] = _epoch(since)
        if cursor:
            params["c"] = _decode_cursor(cursor)
        sid = stream_id(stream, target)
        # a category name may contain "/", which FreshRSS would read as a path separator
        quoted = LABEL + quote(sid[len(LABEL) :], safe="") if sid.startswith(LABEL) else sid
        result: dict[str, Any] = self._get_json(f"{API}/stream/contents/{quoted}", params)
        return result

    def list_articles(
        self,
        stream: str = "unread",
        target: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        since: str | None = None,
        order: str = "newest",
        unread_only: bool = False,
        max_chars: int = 500,
    ) -> dict[str, Any]:
        page = self._page(stream, target, limit, cursor, since, order, unread_only)
        continuation = page.get("continuation")
        return {
            "articles": [_article(item, max_chars) for item in page.get("items", [])],
            "next_cursor": _encode_cursor(continuation) if continuation else None,
        }

    def get_article(self, article_id: str, max_chars: int = 4000) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"{API}/stream/items/contents",
            params={"output": "json"},
            data=[("i", valid_article_id(article_id))],
        )
        items = _json(response, "items")
        if not items:
            raise FreshRSSError(f"article not found: {article_id}")
        return _article(items[0], max_chars)

    def search_articles(
        self,
        query: str,
        stream: str = "all",
        target: str | None = None,
        unread_only: bool = False,
        max_scan: int = 200,
        limit: int = 20,
        max_chars: int = 500,
    ) -> dict[str, Any]:
        needle = query.lower()
        results: list[dict[str, Any]] = []
        scanned = 0
        cursor: str | None = None
        truncated = False
        while True:
            page = self._page(
                stream,
                target,
                min(MAX_PAGE, max_scan - scanned),
                cursor,
                None,
                "newest",
                unread_only,
            )
            for item in page.get("items", []):
                scanned += 1
                if needle in f"{item.get('title', '')}\n{_body(item)}".lower():
                    results.append(_article(item, max_chars))
                    if len(results) >= limit:
                        truncated = True
                        break
            continuation = page.get("continuation")
            if truncated or not continuation or not page.get("items"):
                break
            if scanned >= max_scan:
                truncated = True
                break
            cursor = _encode_cursor(continuation)
        return {"articles": results, "scanned": scanned, "truncated": truncated}

    def edit_tags(self, ids: list[str], add: str | None = None, remove: str | None = None) -> int:
        data: Form = [("i", valid_article_id(item)) for item in ids]
        if add:
            data.append(("a", add))
        if remove:
            data.append(("r", remove))
        self._post_ok(f"{API}/edit-tag", data)
        return len(ids)

    def subscribe(
        self, url: str, title: str | None = None, category: str | None = None
    ) -> dict[str, Any]:
        feed_url(url)
        before = self._subscriptions()
        existing = next((sub for sub in before if sub.get("url") == url), None)
        if existing:
            return {"subscribed": url, "feed_id": existing["id"], "already_subscribed": True}
        data: Form = [("ac", "subscribe"), ("s", f"feed/{url}")]
        if title:
            data.append(("t", title))
        if category:
            data.append(("a", label(category)))
        self._post_ok(f"{API}/subscription/edit", data, timeout=SLOW_TIMEOUT)
        known = {sub["id"] for sub in before}
        added = [sub["id"] for sub in self._subscriptions() if sub["id"] not in known]
        return {"subscribed": url, "feed_id": added[0] if added else None}

    def edit_feed(
        self, feed: str, title: str | None = None, category: str | None = None
    ) -> dict[str, str]:
        target = feed_id(feed)
        data: Form = [("ac", "edit"), ("s", target)]
        if title:
            data.append(("t", title))
        if category is not None:
            data.append(("a", label(category)))
        self._post_ok(f"{API}/subscription/edit", data)
        return {"edited": target}

    def unsubscribe(self, feed: str) -> dict[str, str]:
        target = feed_id(feed)
        self._post_ok(f"{API}/subscription/edit", [("ac", "unsubscribe"), ("s", target)])
        return {"unsubscribed": target}

    def rename_category(self, name: str, new_name: str) -> dict[str, str]:
        self._post_ok(f"{API}/rename-tag", [("s", label(name)), ("dest", label(new_name))])
        return {"renamed": name, "to": new_name}

    def delete_category(self, name: str) -> dict[str, str]:
        self._post_ok(f"{API}/disable-tag", [("s", label(name))])
        return {"deleted": name}

    def mark_all_read(self, stream: str, target: str | None) -> dict[str, str]:
        target_id = stream_id(stream, target)
        newest = self._page(stream, target, 1, None, None, "newest", False).get("items", [])
        if newest:
            # FreshRSS marks entries with id <= ts, and ids come from its clock, so cut off at
            # the newest article's own id instead of this machine's clock.
            cutoff = str(int(newest[0]["id"].rsplit("/", 1)[-1], 16))
            self._post_ok(f"{API}/mark-all-as-read", [("s", target_id), ("ts", cutoff)])
        return {"marked_read": target_id}

    def export_opml(self) -> dict[str, str]:
        return {"opml": self._request("GET", f"{API}/subscription/export").text}

    def import_opml(self, opml: str) -> dict[str, str]:
        if OPML_NETWORK_OPTIONS.search(opml):
            raise FreshRSSError(
                "OPML may not set per-feed network options (CURLOPT_*) or dynamic OPML sources "
                "(opmlUrl): FreshRSS honours them for API imports"
            )
        self._post_ok(f"{API}/subscription/import", [], content=opml.encode(), timeout=SLOW_TIMEOUT)
        return {"imported": "ok"}
