# GODMODE V4 / Signal Lab — Clean Rebuild

## Safety rule
This branch is the rebuild workspace. Do not connect it to the production Render service until feed diagnostics, UI smoke tests, and strategy tests pass. Production remains on its current deployment during development.

## Preserve
- Existing GODMODE V4 dashboard design and chart controls from the embedded `PAGE` in `main.py`.
- Desire identity, countdown/entry alerts, one-signal-at-a-time gate, and bundled voice/audio assets already in the repository.
- Pocket Option read-only connection and symbol catalog integration.
- Existing `godmode_strategy.py`, `godmode_enhancements.py`, and `hybrid_engine.py` indicator/strategy work as compatibility modules while refactoring.
- Fractal-3, Williams Alligator, +DI/-DI (DI length 7, smoothing 14), early crossover/expansion watch, fakeout filters, multi-timeframe controls, 90+/100 preferred quality threshold, and alert cancellation when the setup weakens/reverses.
- Signal-only operation. Never call order-placement methods.

## Non-negotiable feed rules
- Never present synthetic/demo bars as live data or use them for actionable signals.
- A feed is live only when valid broker candles or real ticks have been verified and are fresh.
- Surface connection, subscription, tick heartbeat, historical-candle response, and last-bar timestamps separately in diagnostics.
- A connected WebSocket or synchronized server clock alone does not mean market data is flowing.
- Never claim the feed works until diagnostics report real bars and the UI renders them.

## Rebuild stages
1. Inventory current routes, UI/audio assets, strategy modules, Render settings assumptions, and dependencies.
2. Separate broker transport/feed normalization from API routes and strategy evaluation.
3. Add deterministic tests for candle normalization, stale feed handling, no-synthetic-data behavior, strategy one-signal gate, early DI warnings, and alert cancellation.
4. Keep the existing UI and voice behavior while connecting it to the refactored API.
5. Verify health, status, diagnostics, market endpoint, chart rendering, and voice asset manifest.
6. Only after verification, deploy the rebuild to production.

## Current blocker to solve first
Diagnostics previously showed a recognized and connected Pocket Option session with synchronized server time and an exact `EURUSD_otc` symbol match, but `bar_count: 0`. This means transport status was being reported as healthy without verified market bars. The rebuild must expose which of subscription, realtime ticks, or historical candles is failing rather than masking it.
