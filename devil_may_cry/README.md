# DEVIL MAY CRY — Independent Pocket Option Signal Lab

This is an isolated project folder on branch `devil-may-cry-independent-20261119`. It does not modify Mega Man, Godmode, Sonic, Ichigo, or any other bot's source or Render service.

## Runtime
- API service: `devil-may-cry-api`
- Dashboard route: `/dashboard`
- Default timeframe: 10 seconds
- Signal-only; automatic execution is hard-disabled (`automatic_execution: false`)
- Uses the same direct Pocket Option feed adapter copied as a baseline, but runs in its own Render service and requires its own private `PO_AUTH_JSON` environment variable.

## Strategy settings
- Mega Man Alligator baseline: Jaw 13/8, Teeth 8/5, Lips 5/3; Fractal 3; ADX/DI wide-X, velocity, acceleration and fakeout logic.
- Aroon: period 30.
- OSMA: fast EMA 30, slow EMA 60, signal SMA 30.
- Weighted setup-strength score: DI/ADX 35, Alligator 20, Aroon 15, OSMA 15, Fractal 3 15.
- Signal threshold: 70/100. The score is indicator confluence, not a win probability.

## Required private Render environment
Set `PO_AUTH_JSON` on the DEVIL MAY CRY API service using your own Pocket Option session JSON. Do not commit credentials or paste them into source files. This service cannot read the secret stored on Mega Man's separate Render service.

The API defaults to EURUSD_otc and a 10-second timeframe. The live feed must verify before signals are enabled. No demo candles are substituted for missing live data.
