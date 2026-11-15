# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project uses
[ZeroVer](https://0ver.org/): versions stay `0.x.y`; breaking changes bump the minor
version, features and fixes bump the patch version.

## [Unreleased]

## [0.1.0]

### Added

- MCP server for FreshRSS over the Google Reader API with 18 `freshrss_` tools: list feeds,
  categories, articles and unread counts, get and search articles, export OPML, mark read/unread,
  star/unstar, subscribe, edit feeds, rename categories, and (behind `confirm=true`) unsubscribe,
  delete categories, import OPML and mark everything read.
- stdio transport (default) and SSE transport with a required bearer token.
- Read-only mode, `*_FILE` variants for secrets.
- Container image for `linux/amd64` and `linux/arm64` with provenance and SBOM attestations.

[Unreleased]: https://github.com/hrsather/freshrss-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/hrsather/freshrss-mcp/releases/tag/v0.1.0
