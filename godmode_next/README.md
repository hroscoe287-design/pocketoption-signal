# GODMODE NEXT — isolated rebuild

This directory is an independent signal-only rebuild scaffold. It does not replace the production GODMODE V4 files.

## Render preview
Create a separate Render Web Service from branch `godmode-clean-rebuild`, set **Root Directory** to `godmode_next`, Build Command to `pip install -r requirements.txt`, and Start Command to `uvicorn main:app --host 0.0.0.0 --port $PORT`.

No live provider is assumed by default. Configure `PO_AUTH_JSON` only as a private Render environment variable after confirming the authorized feed/session. Never commit credentials. Until a real feed adapter is implemented and validated, the app reports live data as unavailable and emits no signals.

This is an experimental indicator research tool, not financial advice or a profitability claim. It never places trades.
