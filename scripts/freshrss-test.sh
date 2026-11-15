#!/usr/bin/env bash
# Start or stop a throwaway FreshRSS for the integration tests.
#   scripts/freshrss-test.sh up     then: FRESHRSS_TEST_URL=http://localhost:8081 uv run pytest -m integration --no-cov
#   scripts/freshrss-test.sh down
set -eu

NAME=freshrss-mcp-test
URL=http://localhost:8081

case "${1:-}" in
  up)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" -p 8081:80 \
      --add-host host.docker.internal:host-gateway \
      -e INTERNAL_HOST_ALLOWLIST='*' \
      -e FRESHRSS_INSTALL="--api-enabled --base-url $URL --db-type sqlite --language en --default-user admin" \
      -e FRESHRSS_USER="--user admin --password adminpassword123 --api-password apipassword123 --email admin@example.com --language en" \
      freshrss/freshrss:1.30.4 >/dev/null
    i=0
    until [ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/api/greader.php" || true)" = "200" ]; do
      i=$((i + 1))
      if [ "$i" -gt 90 ]; then
        docker logs "$NAME" >&2
        echo "FreshRSS did not start" >&2
        exit 1
      fi
      sleep 1
    done
    echo "FRESHRSS_TEST_URL=$URL"
    ;;
  down)
    docker rm -f "$NAME" >/dev/null
    ;;
  *)
    echo "usage: freshrss-test.sh up|down" >&2
    exit 2
    ;;
esac
