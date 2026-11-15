import threading
from collections.abc import Iterator
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from freshrss_mcp.client import FreshRSSClient
from freshrss_mcp.config import Settings

from .support import CREDENTIAL, USERNAME, FakeGreader


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def fake() -> FakeGreader:
    return FakeGreader()


@pytest.fixture
def freshrss_url(fake: FakeGreader) -> Iterator[str]:
    httpd = make_server("127.0.0.1", 0, fake, handler_class=_QuietHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def settings(freshrss_url: str) -> Settings:
    return Settings(url=freshrss_url, username=USERNAME, api_password=CREDENTIAL)


@pytest.fixture
def client(settings: Settings) -> FreshRSSClient:
    return FreshRSSClient(settings)
