#!/usr/bin/env bash
set -euo pipefail

curl -fsS \
  --resolve www.tradingview.com:443:108.157.60.62 \
  -o /dev/null \
  https://www.tradingview.com

scanner_status="$(
  curl -s \
    --resolve scanner.tradingview.com:443:3.169.107.123 \
    -o /dev/null \
    -w '%{http_code}' \
    https://scanner.tradingview.com
)"
[ "$scanner_status" = "404" ]

printf '%s\n' "live smoke ok"
