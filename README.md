# Future Trading Bot

This project includes:

- An AI-driven ETH futures bot (`ai_eth_30m_bot.py`) that blends technical and news signals.
- A FastAPI dashboard (`webapp.py`) with a simple static password (`1.618`) to view bot status, PnL, news health, and tweak indicator thresholds.
- A Telegram control bot (`telegram_bot.py`) for running, stopping, retraining flags, and updating indicator thresholds from chat commands.

## Quick start

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Run the dashboard (protected by password `1.618`):

```bash
uvicorn webapp:app --host 0.0.0.0 --port 8000
```

3. Start the Telegram control bot (requires `TELEGRAM_BOT_TOKEN` and optional `TELEGRAM_CHAT_ID`):

```bash
python telegram_bot.py
```

4. Launch the trading loop:

```bash
python ai_eth_30m_bot.py
```

## Dashboard

- Login with the static password `1.618`.
- View status, daily PnL, budget PnL, news fetch diagnostics, and recent commands.
- Send run/stop/retrain commands, update indicator thresholds, and trigger a news fetch test from the UI.

## Telegram commands

- `/run`, `/stop`, `/retrain` – update bot status flags
- `/status` – view current status and thresholds
- `/set <indicator> <value>` – update indicator thresholds (e.g. `/set min_price_model_proba 0.6`)

All commands update the shared `data/state.json`, which the strategy reads to honor stop flags and indicator overrides.
