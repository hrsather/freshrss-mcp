# freshrss-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant read, triage and manage
feeds in your own [FreshRSS](https://freshrss.org) instance. It talks to FreshRSS through its Google
Reader compatible API, so it works with any FreshRSS server that has the API enabled.

- 18 tools: read articles, search, mark read/starred, subscribe, organise categories, OPML.
- Destructive tools refuse to run unless called with `confirm=true`.
- Read-only mode that does not register any write tool.
- stdio (default) or SSE with a required bearer token.
- Article text is plain text, cut to a size you control, with `truncated` always reported.
- Article ids are FreshRSS's real ids and are stable across restarts.

## FreshRSS setup

1. In FreshRSS go to **Settings > Authentication** and enable **Allow API access**.
2. Go to **Settings > Profile** and set an **API password**. This is separate from your login
   password and can be changed at any time to revoke access.
3. The API endpoint is `<your FreshRSS URL>/api/greader.php`. Use the final URL (for example the
   `https://` one): the server does not follow redirects.

Tested against FreshRSS 1.30.4. The minimum supported version is not documented upstream.

## Install

Container (linux/amd64 and linux/arm64):

```sh
docker run --rm -i \
  -e FRESHRSS_URL=https://rss.example.com \
  -e FRESHRSS_USERNAME=me \
  -e FRESHRSS_API_PASSWORD=... \
  ghcr.io/hrsather/freshrss-mcp:0.1.0
```

From source with [uv](https://docs.astral.sh/uv/):

```sh
uvx --from git+https://github.com/hrsather/freshrss-mcp freshrss-mcp
```

## Configuration

Environment variables only.

| Variable | Required | Description |
| --- | --- | --- |
| `FRESHRSS_URL` | yes | Base URL of FreshRSS, e.g. `https://rss.example.com` |
| `FRESHRSS_USERNAME` | yes | FreshRSS username |
| `FRESHRSS_API_PASSWORD` | yes | The API password (not the login password) |
| `MCP_BEARER_TOKEN` | for SSE | Bearer token clients must send over SSE |
| `FRESHRSS_READ_ONLY` | no | `true` removes every write and destructive tool |

`FRESHRSS_URL`, `FRESHRSS_USERNAME`, `FRESHRSS_API_PASSWORD` and `MCP_BEARER_TOKEN` also accept a
`_FILE` variant (for example `FRESHRSS_API_PASSWORD_FILE=/run/secrets/freshrss`) that reads the
value from a file.

## Run

```sh
freshrss-mcp                                            # stdio
freshrss-mcp --transport sse --host 0.0.0.0 --port 8080 # SSE, needs MCP_BEARER_TOKEN
```

The SSE endpoint is `/sse`; send `Authorization: Bearer <MCP_BEARER_TOKEN>`.

Client configuration (stdio):

```json
{
  "mcpServers": {
    "freshrss": {
      "command": "docker",
      "args": ["run", "--rm", "-i",
               "-e", "FRESHRSS_URL", "-e", "FRESHRSS_USERNAME", "-e", "FRESHRSS_API_PASSWORD",
               "ghcr.io/hrsather/freshrss-mcp:0.1.0"],
      "env": {
        "FRESHRSS_URL": "https://rss.example.com",
        "FRESHRSS_USERNAME": "me",
        "FRESHRSS_API_PASSWORD": "..."
      }
    }
  }
}
```

## Tools

All tools return JSON and carry MCP read-only / destructive hints. Errors are plain, actionable
strings.

Read:

| Tool | Purpose |
| --- | --- |
| `freshrss_list_feeds` | Feeds with id, title, URL, categories and unread count |
| `freshrss_list_categories` | Categories with unread counts |
| `freshrss_get_unread_counts` | Total, per-feed and per-category unread |
| `freshrss_list_articles` | `stream` (`unread`, `all`, `starred`, `feed`, `category`), `target`, `limit` (max 50), `cursor`, `since`, `order`, `unread_only`, `max_chars` |
| `freshrss_get_article` | One article by id, text up to `max_chars` |
| `freshrss_search_articles` | Case-insensitive search over title and body; reports `scanned` and `truncated` |
| `freshrss_export_opml` | All subscriptions as OPML |

Write (not registered in read-only mode):

| Tool | Purpose |
| --- | --- |
| `freshrss_mark_read`, `freshrss_mark_unread` | By article id (max 100 per call) |
| `freshrss_star`, `freshrss_unstar` | By article id (max 100 per call) |
| `freshrss_subscribe` | Subscribe to a feed URL, optionally with title and category |
| `freshrss_edit_feed` | Rename a feed and/or move it to a category |
| `freshrss_rename_category` | Rename a category |

Destructive (not registered in read-only mode; refuse unless `confirm=true`):

| Tool | Purpose |
| --- | --- |
| `freshrss_unsubscribe` | Unsubscribe from a feed and delete its articles |
| `freshrss_delete_category` | Delete a category |
| `freshrss_import_opml` | Import subscriptions from OPML (max 1 MB) |
| `freshrss_mark_all_read` | Mark every article in a stream read |

Feed ids look like `feed/12`; article ids look like `tag:google.com,2005:reader/item/0006...`.
Pass `next_cursor` from one `freshrss_list_articles` result as `cursor` to get the next page.

## Security

- The API password and bearer token are never logged or returned in tool output or errors.
- SSE requires a bearer token (compared in constant time). Do not expose the SSE port publicly
  without TLS in front of it.
- Destructive tools need an explicit `confirm=true`; set `FRESHRSS_READ_ONLY=true` if the
  assistant should never change anything.
- The container runs as a non-root user and holds no state.

Report vulnerabilities as described in [SECURITY.md](SECURITY.md).

## Known limits

- **Search is client-side.** FreshRSS has no server-side search over the Google Reader API, so
  `freshrss_search_articles` fetches pages and filters them. It scans at most `max_scan` articles
  (default 200, max 500), newest first, and says so in `scanned` and `truncated`.
- **Local feed URLs.** FreshRSS refuses to subscribe to feeds on private addresses unless its
  `INTERNAL_HOST_ALLOWLIST` is set; this surfaces as an HTTP 400 error.
- Only the Google Reader API is supported (no Fever API) and only one FreshRSS user per server.
- Greader implementations differ between FreshRSS versions; only 1.30.4 is tested.

## Development

```sh
uv sync
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest
```

Unit and protocol tests run against an in-memory fake of the API. Integration tests run against a
real FreshRSS and are skipped unless `FRESHRSS_TEST_URL` is set; see `tests/test_integration.py`
and the `integration` job in `.github/workflows/ci.yml`.

## License

MIT
