# GODMODE V4 — Pocket Option Signal Lab

A separate, signal-only Pocket Option research dashboard with a live-style candlestick chart, BUY/SELL/WAIT panel, multi-timeframe controls, and a transparent original confluence strategy. It does not place trades.

## Deploy on Render
- Runtime: Python
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`

The app starts with clearly labeled synthetic demo candles so the interface works immediately. Demo data is not live market data. Real Pocket Option OTC feeds require a supported, authorized feed/session; do not paste account credentials into public code or commit them to GitHub.

## Strategy
The initial prototype uses EMA trend alignment, RSI momentum, and MACD histogram direction to produce a transparent illustrative confluence score. It is not the proprietary Godmode V4 algorithm and has no claim of profitability. Validate with recorded data and paper trading before considering real-money use.

## DESIRE ElevenLabs voice

DESIRE uses the ElevenLabs text-to-speech API from the Python server; the API key is never sent to the browser or stored in source code. In the Render service environment, add `ELEVENLABS_API_KEY` using your private ElevenLabs API key. Optionally set `DESIRE_VOICE_ID` to a female voice ID from your ElevenLabs account; if omitted, the app uses its configured default female voice. Save the environment variable and redeploy/restart the service. The dashboard voice is enabled by tapping **ENABLE SOUND** in the browser.

The entry countdown announces the available preparation window for longer timeframes, then speaks the final `5, 4, 3, 2, 1, now` sequence. Voice alerts are advisory only; the app remains signal-only and never places trades. A valid provider account/API key and available ElevenLabs credits are required for hosted speech.
