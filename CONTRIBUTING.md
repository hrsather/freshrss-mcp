# Contributing

1. Install [uv](https://docs.astral.sh/uv/) and run `uv sync`.
2. Branch from `main` and keep the change focused.
3. Run `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` and `uv run pytest`. These
   run against an in-memory fake of the Google Reader API; no FreshRSS is needed. To also run the
   integration tests, start a FreshRSS container as in the `integration` job of
   `.github/workflows/ci.yml`, then run `uv run pytest -m integration --no-cov` with
   `FRESHRSS_TEST_URL`, `FRESHRSS_TEST_USERNAME` and `FRESHRSS_TEST_API_PASSWORD` set.
4. Prefix commit messages with `feat:` or `fix:`.
5. Use synthetic data only: `example.com` URLs and placeholder feeds and articles.
6. Open a pull request. CI must pass.

Releases follow [ZeroVer](https://0ver.org/) and are cut by tagging `vX.Y.Z`.
