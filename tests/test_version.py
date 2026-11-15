import tomllib
from pathlib import Path

from freshrss_mcp import __version__


def test_package_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == __version__
