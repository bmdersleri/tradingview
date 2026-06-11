# Browser Smoke Checklist

Run these on the target server with a valid TradingView session.

1. `tvcli auth status --json`
2. `tvcli chart shot BIST:THYAO --interval 1d --out /tmp/t.png --json`
3. `tvcli ui alert create BIST:THYAO --condition Crossing --value 320 --json`
4. `tvcli ui alert list --json`
5. `tvcli ui watchlist add BIST:THYAO NASDAQ:NVDA --list main --json`
6. `tvcli ui watchlist export --list main --json`
7. `tvcli ui pine push --file script.pine --name demo --add-to-chart --json`

Expected outcomes:

- JSON envelope with `ok: true`
- No selector failures
- Exit code `6` only when TradingView changes the DOM contract
- Screenshot output is non-empty and readable
