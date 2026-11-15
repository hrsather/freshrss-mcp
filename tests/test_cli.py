import pytest

from freshrss_mcp import cli


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRESHRSS_URL", "http://freshrss.example.com")
    monkeypatch.setenv("FRESHRSS_USERNAME", "u")
    monkeypatch.setenv("FRESHRSS_API_PASSWORD", "pw")
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("MCP_BEARER_TOKEN_FILE", raising=False)


def test_missing_config_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("FRESHRSS_URL")
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    assert "FRESHRSS_URL" in capsys.readouterr().err


def test_sse_requires_token(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--transport", "sse"])
    assert exc.value.code == 2
    assert "MCP_BEARER_TOKEN" in capsys.readouterr().err


def test_stdio_runs_server(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []
    monkeypatch.setattr(
        "mcp.server.mcpserver.MCPServer.run", lambda self, transport: ran.append(transport)
    )
    cli.main([])
    assert ran == ["stdio"]


def test_sse_runs_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("MCP_BEARER_TOKEN", "tok")
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: calls.append(kw))
    cli.main(["--transport", "sse", "--host", "127.0.0.2", "--port", "9000"])
    assert calls == [{"host": "127.0.0.2", "port": 9000, "log_level": "warning"}]


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == cli.__version__
