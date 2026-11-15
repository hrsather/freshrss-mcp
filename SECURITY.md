# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub security advisories](https://github.com/hrsather/freshrss-mcp/security/advisories/new).
Do not open a public issue. Expect an initial response within a week.

## Supported versions

Only the latest release is supported.

## Handling credentials

This server needs a FreshRSS username and an API password (set under Settings > Profile in
FreshRSS). Never share them in issues or logs. If one leaks, change the API password in FreshRSS,
which revokes the old one immediately. Rotate `MCP_BEARER_TOKEN` too if it leaked.
