import base64
import binascii
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from freshrss_mcp.config import Settings
from freshrss_mcp.text import strip_html, truncate

READ = "user/-/state/com.google/read"
STARRED = "user/-/state/com.google/starred"
READING_LIST = "user/-/state/com.google/reading-list"
LABEL = "user/-/label/"
API = "/reader/api/0"

MAX_PAGE = 50
GET_RETRIES = 2
STREAMS = ("unread", "all", "starred", "feed", "category")

Form = list[tuple[str, str]]


class FreshRSSError(Exception):
    pass


def feed_id(feed: str) -> str:
    return feed if feed.startswith("feed/") else f"feed/{feed}"


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
    if not isinstance(seconds, int | float):
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat()


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
    return strip_html(content.get("content", ""))


def _article(item: dict[str, Any], max_chars: int) -> dict[str, Any]:
    categories: list[str] = item.get("categories", [])
    text = _body(item)
    shown, truncated = truncate(text, max_chars)
    links = item.get("canonical") or item.get("alternate") or [{}]
    origin = item.get("origin", {})
    return {
        "id": item["id"],
        "title": item.get("title", ""),
        "url": links[0].get("href"),
        "feed_id": origin.get("streamId"),
        "feed_title": origin.get("title"),
        "author": item.get("author") or None,
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
        self._http = httpx.Client(
            base_url=f"{settings.url}/api/greader.php",
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
        )
        self._auth: str | None = None
        self._token: str | None = None

    def _fail(self, response: httpx.Response) -> FreshRSSError:
        status = response.status_code
        if 300 <= status < 400:
            where = response.headers.get("location", "another URL")
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
            response = self._send("GET", f"{API}/token")
            self._token = response.text.strip()
        return self._token

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        data: Form | None = None,
        content: bytes | None = None,
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
                )
            except httpx.HTTPError as exc:
                if attempt + 1 < attempts:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise FreshRSSError(
                    f"cannot reach FreshRSS at {self._settings.url} ({type(exc).__name__})"
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
    ) -> httpx.Response:
        for retry in (True, False):
            body, query = data, params
            if write:
                token = self._write_token()
                if content is None:
                    body = [("T", token), *(data or [])]
                else:
                    query = {**(params or {}), "T": token}
            response = self._send(method, path, params=query, data=body, content=content)
            if (
                response.status_code in {400, 401}
                and retry
                and (write or response.status_code == 401)
            ):
                self._auth = None
                self._token = None
                continue
            if response.status_code != 200:
                raise self._fail(response)
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _get_json(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        response = self._request("GET", path, params={"output": "json", **(params or {})})
        try:
            return response.json()
        except ValueError as exc:
            raise FreshRSSError("FreshRSS returned an unexpected (non-JSON) response") from exc

    def _post_ok(self, path: str, data: Form, content: bytes | None = None) -> None:
        response = self._request("POST", path, data=data, content=content, write=True)
        if not response.text.strip().startswith("OK"):
            raise FreshRSSError(f"FreshRSS rejected the request: {response.text.strip()[:200]}")

    def _subscriptions(self) -> list[dict[str, Any]]:
        return list(self._get_json(f"{API}/subscription/list")["subscriptions"])

    def _unread_by_id(self) -> dict[str, int]:
        counts = self._get_json(f"{API}/unread-count")["unreadcounts"]
        return {entry["id"]: int(entry["count"]) for entry in counts}

    def list_feeds(self) -> list[dict[str, Any]]:
        unread = self._unread_by_id()
        return [
            {
                "id": sub["id"],
                "title": sub.get("title", ""),
                "url": sub.get("url"),
                "site_url": sub.get("htmlUrl"),
                "categories": [c["label"] for c in sub.get("categories", [])],
                "unread": unread.get(sub["id"], 0),
            }
            for sub in self._subscriptions()
        ]

    def list_categories(self) -> list[dict[str, Any]]:
        unread = self._unread_by_id()
        tags = self._get_json(f"{API}/tag/list")["tags"]
        return [
            {"name": tag["id"][len(LABEL) :], "unread": unread.get(tag["id"], 0)}
            for tag in tags
            if tag["id"].startswith(LABEL)
        ]

    def get_unread_counts(self) -> dict[str, Any]:
        unread = self._unread_by_id()
        titles = {sub["id"]: sub.get("title", "") for sub in self._subscriptions()}
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
        path = f"{API}/stream/contents/{quote(stream_id(stream, target), safe='/')}"
        result: dict[str, Any] = self._get_json(path, params)
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
            data=[("i", article_id)],
        )
        items = response.json().get("items", [])
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
            if truncated or not continuation:
                break
            if scanned >= max_scan:
                truncated = True
                break
            cursor = _encode_cursor(continuation)
        return {"articles": results, "scanned": scanned, "truncated": truncated}

    def edit_tags(self, ids: list[str], add: str | None = None, remove: str | None = None) -> int:
        data: Form = [("i", item) for item in ids]
        if add:
            data.append(("a", add))
        if remove:
            data.append(("r", remove))
        self._post_ok(f"{API}/edit-tag", data)
        return len(ids)

    def subscribe(
        self, url: str, title: str | None = None, category: str | None = None
    ) -> dict[str, Any]:
        data: Form = [("ac", "subscribe"), ("s", f"feed/{url}")]
        if title:
            data.append(("t", title))
        if category:
            data.append(("a", label(category)))
        self._post_ok(f"{API}/subscription/edit", data)
        found = [sub for sub in self._subscriptions() if sub.get("url") == url]
        return {"subscribed": url, "feed_id": found[0]["id"] if found else None}

    def edit_feed(
        self, feed: str, title: str | None = None, category: str | None = None
    ) -> dict[str, str]:
        target = feed_id(feed)
        data: Form = [("ac", "edit"), ("s", target)]
        if title:
            data.append(("t", title))
        if category is not None:
            current = next((s for s in self._subscriptions() if s["id"] == target), None)
            if current is None:
                raise FreshRSSError(f"feed not found: {target}")
            data.extend(("r", c["id"]) for c in current.get("categories", []))
            if category:
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
        now_usec = str(int(time.time() * 1_000_000))
        self._post_ok(f"{API}/mark-all-as-read", [("s", target_id), ("ts", now_usec)])
        return {"marked_read": target_id}

    def export_opml(self) -> dict[str, str]:
        return {"opml": self._request("GET", f"{API}/subscription/export").text}

    def import_opml(self, opml: str) -> dict[str, str]:
        self._post_ok(f"{API}/subscription/import", [], content=opml.encode())
        return {"imported": "ok"}
