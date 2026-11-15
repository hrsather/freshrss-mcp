import logging

import pytest

from freshrss_mcp.client import FreshRSSClient
from freshrss_mcp.config import Settings
from freshrss_mcp.server import build_server

from .support import OPML, FakeGreader


def test_secrets_never_reach_the_logs(
    settings: Settings, fake: FakeGreader, caplog: pytest.LogCaptureFixture
) -> None:
    # building the server turns on root INFO logging, as it does when run for real
    build_server(settings)
    client = FreshRSSClient(settings)
    with caplog.at_level(logging.DEBUG):
        client.list_feeds()
        client.edit_tags(["tag:google.com,2005:reader/item/0000000000000001"], add="x")
        client.import_opml(OPML)
    assert fake.valid_token
    assert fake.valid_token not in caplog.text
    assert fake.valid_auth
    assert fake.valid_auth not in caplog.text
    assert settings.api_password not in caplog.text
