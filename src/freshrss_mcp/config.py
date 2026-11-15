import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(Exception):
    pass


def _read(env: Mapping[str, str], name: str) -> str | None:
    file_name = env.get(f"{name}_FILE")
    if file_name:
        try:
            return Path(file_name).read_text().strip()
        except OSError as exc:
            raise ConfigError(f"cannot read {name}_FILE: {exc.strerror}") from exc
    value = env.get(name)
    return value.strip() if value else None


def _flag(env: Mapping[str, str], name: str) -> bool:
    value = env.get(name, "").strip().lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"", "0", "false", "no"}:
        return False
    raise ConfigError(f"{name} must be true or false")


@dataclass(frozen=True)
class Settings:
    url: str
    username: str
    api_password: str = field(repr=False)
    bearer_token: str | None = field(default=None, repr=False)
    read_only: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        url = _read(env, "FRESHRSS_URL")
        username = _read(env, "FRESHRSS_USERNAME")
        api_password = _read(env, "FRESHRSS_API_PASSWORD")
        if not url or not username or not api_password:
            raise ConfigError(
                "FRESHRSS_URL, FRESHRSS_USERNAME and FRESHRSS_API_PASSWORD are required"
            )
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ConfigError("FRESHRSS_URL must be an http(s) URL such as https://rss.example.com")
        if parts.username or parts.password:
            raise ConfigError("FRESHRSS_URL must not contain credentials")
        return cls(
            url=url.rstrip("/"),
            username=username,
            api_password=api_password,
            bearer_token=_read(env, "MCP_BEARER_TOKEN"),
            read_only=_flag(env, "FRESHRSS_READ_ONLY"),
        )
