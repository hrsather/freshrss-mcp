from pathlib import Path

import pytest

from freshrss_mcp.config import ConfigError, Settings

BASE = {
    "FRESHRSS_URL": "https://rss.example.com/",
    "FRESHRSS_USERNAME": "u",
    "FRESHRSS_API_PASSWORD": "p",
}


def test_from_env() -> None:
    settings = Settings.from_env({**BASE, "MCP_BEARER_TOKEN": "tok", "FRESHRSS_READ_ONLY": "true"})
    assert settings.url == "https://rss.example.com"
    assert settings.username == "u"
    assert settings.bearer_token == "tok"
    assert settings.read_only is True


def test_defaults() -> None:
    settings = Settings.from_env(BASE)
    assert settings.bearer_token is None
    assert settings.read_only is False


def test_repr_hides_secrets() -> None:
    text = repr(
        Settings.from_env({**BASE, "FRESHRSS_API_PASSWORD": "hunter2", "MCP_BEARER_TOKEN": "tok3n"})
    )
    assert "hunter2" not in text
    assert "tok3n" not in text


@pytest.mark.parametrize("missing", list(BASE))
def test_required(missing: str) -> None:
    env = {k: v for k, v in BASE.items() if k != missing}
    with pytest.raises(ConfigError, match="FRESHRSS_URL, FRESHRSS_USERNAME"):
        Settings.from_env(env)


@pytest.mark.parametrize("url", ["rss.example.com", "ftp://rss.example.com", "http://"])
def test_url_must_be_http(url: str) -> None:
    with pytest.raises(ConfigError, match="http"):
        Settings.from_env({**BASE, "FRESHRSS_URL": url})


def test_file_variants(tmp_path: Path) -> None:
    secret = tmp_path / "password"
    secret.write_text("from-file\n")
    env = {k: v for k, v in BASE.items() if k != "FRESHRSS_API_PASSWORD"}
    settings = Settings.from_env({**env, "FRESHRSS_API_PASSWORD_FILE": str(secret)})
    assert settings.api_password == "from-file"


def test_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="FRESHRSS_API_PASSWORD_FILE"):
        Settings.from_env({**BASE, "FRESHRSS_API_PASSWORD_FILE": str(tmp_path / "nope")})


def test_bad_flag() -> None:
    with pytest.raises(ConfigError, match="FRESHRSS_READ_ONLY"):
        Settings.from_env({**BASE, "FRESHRSS_READ_ONLY": "maybe"})


@pytest.mark.parametrize(
    ("value", "expected"), [("1", True), ("yes", True), ("no", False), ("", False)]
)
def test_flag_values(value: str, expected: bool) -> None:
    assert Settings.from_env({**BASE, "FRESHRSS_READ_ONLY": value}).read_only is expected
