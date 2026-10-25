# Mega Man Signal API

FastAPI backend for the Mega Man signal-only UI. It provides `/health` and `/analyze`. The analysis endpoint accepts recent OHLC candles and returns UP, DOWN, or WAIT using a transparent EMA/momentum/candle-direction heuristic. It does not claim a verified win probability and never places trades.

## Environment
- `MEGAMAN_API_KEY`: optional API key; if set, send it as `X-API-Key` on `/analyze`.
- `PO_AUTH_JSON`: optional broker credential variable, health reports only whether it exists; this starter does not parse or use it to connect to Pocket Option.
- `CORS_ORIGINS`: comma-separated allowed front-end origins.

## Run
`pip install -r requirements.txt && uvicorn main:app --host 0.0.0.0 --port 10000`

Important: the frontend must supply actual OHLC candles from a trusted, verified feed. This backend does not yet connect directly to the Pocket Option websocket. Do not expose broker credentials to browser code.