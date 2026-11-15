# Contributing

1. Install [uv](https://docs.astral.sh/uv/) and run `uv sync`.
2. Branch from `main` and keep the change focused.
3. Run `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` and `uv run pytest`. These
   run against an in-memory fake of the Google Reader API; no FreshRSS is needed. To also run the
   integration tests (needs Docker): `scripts/freshrss-test.sh up`, then
   `FRESHRSS_TEST_URL=http://localhost:8081 uv run pytest -m integration --no-cov`, then
   `scripts/freshrss-test.sh down`.
4. Prefix commit messages with `feat:` or `fix:`.
5. Use synthetic data only: `example.com` URLs and placeholder feeds and articles.
6. Open a pull request. CI must pass.

Releases follow [ZeroVer](https://0ver.org/). To release, bump `version` in `pyproject.toml` and
`src/freshrss_mcp/__init__.py`, add a `CHANGELOG.md` entry, and merge. The release workflow then
tests, publishes the image and creates the `vX.Y.Z` tag and GitHub Release; merges that do not
change the version release nothing.
