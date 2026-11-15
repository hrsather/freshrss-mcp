import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs, unquote
from wsgiref.types import StartResponse

USERNAME = "reader"
CREDENTIAL = "api-credential-for-tests"
BEARER = "test-bearer-token-0123456789abcdef"

READ = "user/-/state/com.google/read"
STARRED = "user/-/state/com.google/starred"
LABEL = "user/-/label/"
ITEM = "tag:google.com,2005:reader/item/"

OPML = (
    '<?xml version="1.0"?><opml version="2.0"><body>'
    '<outline text="Imported" xmlUrl="http://feeds.example.com/imported.xml"/></body></opml>'
)


class FakeGreader:
    """A small in-memory stand-in for FreshRSS's Google Reader API, served over WSGI."""

    def __init__(self) -> None:
        self.logins = 0
        self.tokens = 0
        self.valid_auth: str | None = None
        self.valid_token: str | None = None
        self.fail_next: list[int] = []
        self.redirect_login = False
        self.log: list[tuple[str, str, dict[str, list[str]]]] = []
        self.subscriptions: list[dict[str, Any]] = [
            self._sub(1, "Alpha", "alpha", "Tech"),
            self._sub(2, "Beta", "beta", "News"),
        ]
        self.items: list[dict[str, Any]] = [
            self._item(1, 1, "Kubernetes tips", "<p>Run <b>pods</b> well.</p><script>x()</script>"),
            self._item(2, 1, "Gardening", "<p>Tomatoes &amp; basil.</p>"),
            self._item(3, 2, "Election news", "<div>Results are in.</div><div>More soon.</div>"),
            self._item(4, 2, "Long read", "<p>" + "word " * 400 + "</p>"),
        ]
        self.opml_imported = ""

    @staticmethod
    def _sub(number: int, title: str, slug: str, category: str) -> dict[str, Any]:
        return {
            "id": f"feed/{number}",
            "title": title,
            "url": f"http://feeds.example.com/{slug}.xml",
            "htmlUrl": f"http://{slug}.example.com/",
            "categories": [{"id": LABEL + category, "label": category}],
        }

    @staticmethod
    def _item(number: int, feed: int, title: str, html: str) -> dict[str, Any]:
        return {
            "id": f"{ITEM}{number:016x}",
            "title": title,
            "html": html,
            "feed": feed,
            "published": 1_780_000_000 + number * 60,
            "states": set(),
        }

    def add_bulk(self, count: int, feed: int = 1) -> None:
        start = len(self.items) + 1
        for number in range(start, start + count):
            self.items.append(
                self._item(number, feed, f"Bulk {number}", f"<p>bulk body {number}</p>")
            )

    def expire_auth(self) -> None:
        self.valid_auth = None

    def __call__(self, environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        method = environ["REQUEST_METHOD"]
        path = unquote(environ["PATH_INFO"]).removeprefix("/api/greader.php")
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        raw = environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0))
        is_form = "x-www-form-urlencoded" in environ.get("CONTENT_TYPE", "")
        form = parse_qs(raw.decode(), keep_blank_values=True) if is_form else {}
        self.log.append((method, path, {**query, **form}))

        status, body, headers = self._route(method, path, query, form, raw, environ)
        start_response(status, [("Content-Type", "text/plain"), *headers])
        return [body.encode()]

    def _route(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        form: dict[str, list[str]],
        raw: bytes,
        environ: dict[str, Any],
    ) -> tuple[str, str, list[tuple[str, str]]]:
        if path == "/accounts/ClientLogin":
            return self._login(form)
        if self.fail_next:
            code = self.fail_next.pop(0)
            return f"{code} Error", "boom", []
        if environ.get("HTTP_AUTHORIZATION") != f"GoogleLogin auth={self.valid_auth}":
            return "401 Unauthorized", "Unauthorized!", []
        if path == "/reader/api/0/token":
            self.tokens += 1
            self.valid_token = f"token-{self.logins}"
            return "200 OK", self.valid_token, []
        no_token = {"/reader/api/0/stream/items/contents", "/reader/api/0/subscription/import"}
        if method == "POST" and path not in no_token:
            if form.get("T", [""])[0] != self.valid_token:
                return "401 Unauthorized", "Unauthorized!", [("Google-Bad-Token", "true")]
        return self._handle(method, path, query, form, raw)

    def _login(self, form: dict[str, list[str]]) -> tuple[str, str, list[tuple[str, str]]]:
        if self.redirect_login:
            return "302 Found", "", [("Location", "https://rss.example.com/api/greader.php")]
        if form.get("Email") != [USERNAME] or form.get("Passwd") != [CREDENTIAL]:
            return "401 Unauthorized", "Error=BadAuthentication", []
        self.logins += 1
        self.valid_auth = f"{USERNAME}/auth{self.logins}"
        self.valid_token = None
        return "200 OK", f"SID={self.valid_auth}\nLSID=null\nAuth={self.valid_auth}\n", []

    def _json(self, payload: Any) -> tuple[str, str, list[tuple[str, str]]]:
        return "200 OK", json.dumps(payload), []

    def _handle(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        form: dict[str, list[str]],
        raw: bytes,
    ) -> tuple[str, str, list[tuple[str, str]]]:
        base = "/reader/api/0"
        if path == f"{base}/subscription/list":
            return self._json({"subscriptions": self.subscriptions})
        if path == f"{base}/tag/list":
            labels = {c["id"] for s in self.subscriptions for c in s["categories"]}
            return self._json({"tags": [{"id": STARRED}, *({"id": i} for i in sorted(labels))]})
        if path == f"{base}/unread-count":
            return self._json({"unreadcounts": self._counts()})
        if path.startswith(f"{base}/stream/contents/"):
            return self._stream(path.removeprefix(f"{base}/stream/contents/"), query)
        if path == f"{base}/stream/items/contents":
            wanted = form.get("i", [])
            found = [self._render(i) for i in self.items if i["id"] in wanted]
            return self._json({"items": found})
        if path == f"{base}/subscription/export":
            return "200 OK", "<opml><body/></opml>", []
        if path == f"{base}/subscription/import":
            self.opml_imported = raw.decode()
            return "200 OK", "OK", []
        if path == f"{base}/edit-tag":
            return self._edit_tag(form)
        if path == f"{base}/subscription/edit":
            return self._edit_subscription(form)
        if path == f"{base}/rename-tag":
            return self._rename(form)
        if path == f"{base}/disable-tag":
            gone = form["s"][0]
            for sub in self.subscriptions:
                if sub["categories"][0]["id"] == gone:
                    sub["categories"] = [self._category(LABEL)]
            return "200 OK", "OK", []
        if path == f"{base}/mark-all-as-read":
            cutoff = int(form["ts"][0])
            for item in self._select(form["s"][0]):
                if int(item["id"].rsplit("/", 1)[-1], 16) <= cutoff:
                    item["states"].add(READ)
            return "200 OK", "OK", []
        return "404 Not Found", "", []

    def _counts(self) -> list[dict[str, Any]]:
        unread = [i for i in self.items if READ not in i["states"]]
        counts: list[dict[str, Any]] = [
            {"id": "user/-/state/com.google/reading-list", "count": len(unread)}
        ]
        for sub in self.subscriptions:
            mine = [i for i in unread if f"feed/{i['feed']}" == sub["id"]]
            counts.append({"id": sub["id"], "count": len(mine)})
            for cat in sub["categories"]:
                counts.append({"id": cat["id"], "count": len(mine)})
        return counts

    def _select(self, stream: str) -> list[dict[str, Any]]:
        if stream == STARRED:
            return [i for i in self.items if STARRED in i["states"]]
        if stream.startswith("feed/"):
            return [i for i in self.items if f"feed/{i['feed']}" == stream]
        if stream.startswith(LABEL):
            feeds = {
                s["id"]
                for s in self.subscriptions
                if any(c["id"] == stream for c in s["categories"])
            }
            return [i for i in self.items if f"feed/{i['feed']}" in feeds]
        return list(self.items)

    def _render(self, item: dict[str, Any]) -> dict[str, Any]:
        sub = next(s for s in self.subscriptions if s["id"] == f"feed/{item['feed']}")
        return {
            "id": item["id"],
            "title": item["title"],
            "published": item["published"],
            "canonical": [{"href": f"http://example.com/{item['id'][-4:]}"}],
            "summary": {"content": item["html"]},
            "categories": [*sorted(item["states"]), *(c["id"] for c in sub["categories"])],
            "origin": {"streamId": sub["id"], "title": sub["title"]},
        }

    def _stream(
        self, stream: str, query: dict[str, list[str]]
    ) -> tuple[str, str, list[tuple[str, str]]]:
        chosen = self._select(stream)
        if query.get("xt") == [READ]:
            chosen = [i for i in chosen if READ not in i["states"]]
        if "ot" in query:
            chosen = [i for i in chosen if i["published"] >= int(query["ot"][0])]
        chosen.sort(key=lambda i: i["published"], reverse=query.get("r") != ["o"])
        size = int(query.get("n", ["20"])[0])
        offset = int(query.get("c", ["0"])[0])
        page = chosen[offset : offset + size]
        payload: dict[str, Any] = {"items": [self._render(i) for i in page]}
        if offset + size < len(chosen):
            payload["continuation"] = str(offset + size)
        return self._json(payload)

    def _edit_tag(self, form: dict[str, list[str]]) -> tuple[str, str, list[tuple[str, str]]]:
        for item in self.items:
            if item["id"] in form.get("i", []):
                item["states"].update(form.get("a", []))
                item["states"].difference_update(form.get("r", []))
        return "200 OK", "OK", []

    def _edit_subscription(
        self, form: dict[str, list[str]]
    ) -> tuple[str, str, list[tuple[str, str]]]:
        action, stream = form["ac"][0], form["s"][0]
        if action == "subscribe":
            url = stream.removeprefix("feed/")
            if "private" in url or any(s["url"] == url for s in self.subscriptions):
                return "400 Bad Request", "Bad Request!", []
            sub = {
                "id": f"feed/{len(self.subscriptions) + 1}",
                "title": form.get("t", [url])[0],
                "url": url,
                "htmlUrl": url,
                "categories": [self._category(form.get("a", [LABEL])[0])],
            }
            self.subscriptions.append(sub)
        elif action == "unsubscribe":
            self.subscriptions = [s for s in self.subscriptions if s["id"] != stream]
        else:
            sub = next(s for s in self.subscriptions if s["id"] == stream)
            if "t" in form:
                sub["title"] = form["t"][0]
            if "a" in form:
                sub["categories"] = [self._category(form["a"][0])]
        return "200 OK", "OK", []

    @staticmethod
    def _category(stream: str) -> dict[str, str]:
        name = stream.removeprefix(LABEL) or "Uncategorized"
        return {"id": LABEL + name, "label": name}

    def _rename(self, form: dict[str, list[str]]) -> tuple[str, str, list[tuple[str, str]]]:
        old, new = form["s"][0], form["dest"][0]
        for sub in self.subscriptions:
            for cat in sub["categories"]:
                if cat["id"] == old:
                    cat.update(id=new, label=new.removeprefix(LABEL))
        return "200 OK", "OK", []
