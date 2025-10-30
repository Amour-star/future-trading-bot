import os
import time
import requests
import pytz
import ccxt
import yaml
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
import signals

def now_str(tz="Europe/Berlin"):
    return datetime.now(pytz.timezone(tz)).strftime("%Y-%m-%d %H:%M:%S %Z")

def tg(token, chat_id, msg):
    if not token or not chat_id:
        print("[TG disabled]", msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print("Telegram error:", e)

def load_cfg(path="config_ai_eth.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def fetch_candles_df(ex, symbol, tf, limit=500) -> pd.DataFrame:
    ohlcv = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df    = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def set_leverage_margin(ex, symbol, lev, mode):
    try:
        ex.setLeverage(int(lev), symbol, {"marginMode": mode})
    except Exception:
        try:
            ex.setMarginMode(mode, symbol)
            ex.setLeverage(int(lev), symbol)
        except Exception as e:
            print("Leverage/margin warning:", e)

def compute_amount_from_notional(ex, symbol, notional_usdt, price):
    market = ex.market(symbol)
    prec   = market.get("precision", {}).get("amount", 6)
    amt    = notional_usdt / price
    return float(round(amt, prec))

def open_position(ex, cfg, side_dir: str):
    symbol   = cfg["symbol"]
    lev      = int(cfg["leverage"])
    mode     = cfg.get("margin_mode", "isolated")
    entry_ty = cfg.get("entry_type", "market")
    margin   = float(cfg["margin_usdt"])
    notional = margin * lev
    tp_pct   = float(cfg["tp_percent"]) / 100.0
    sl_pct   = float(cfg["sl_percent"]) / 100.0
    tz       = cfg.get("timezone", "Europe/Berlin")
    dry      = bool(cfg.get("dry_run", False))

    last   = float(ex.fetch_ticker(symbol)["last"])
    amount = compute_amount_from_notional(ex, symbol, notional, last)

    order_side = "buy"  if side_dir == "long" else "sell"
    exit_side  = "sell" if side_dir == "long" else "buy"

    if side_dir == "long":
        tp_prev = last * (1 + tp_pct)
        sl_prev = last * (1 - sl_pct)
    else:
        tp_prev = last * (1 - tp_pct)
        sl_prev = last * (1 + sl_pct)

    tg(TG_TOKEN, TG_CHAT,
       f"🧠 <b>AI SIGNAL</b> → {side_dir.upper()}\n"
       f"{symbol} amount≈{amount}\n"
       f"Entry: {entry_ty.upper()} ~ {last:.2f}\n"
       f"TP≈{tp_prev:.2f} | SL≈{sl_prev:.2f}\n"
       f"Time: {now_str(tz)}")

    if dry:
        tg(TG_TOKEN, TG_CHAT, "🧪 DRY-RUN: skip real orders.")
        return

    set_leverage_margin(ex, symbol, lev, mode)

    if entry_ty == "market":
        order       = ex.create_order(symbol, "market", order_side, amount, None, {"reduceOnly": False})
        entry_price = float(order.get("price") or order.get("average") or ex.fetch_ticker(symbol)["last"])
    else:
        order       = ex.create_order(symbol, "limit", order_side, amount, last, {"reduceOnly": False})
        entry_price = last

    if side_dir == "long":
        tp_price = entry_price * (1 + tp_pct)
        sl_price = entry_price * (1 - sl_pct)
    else:
        tp_price = entry_price * (1 - tp_pct)
        sl_price = entry_price * (1 + sl_pct)

    pos_amt   = amount
    tp_params = {
        "reduceOnly":    True,
        "type":          "take_profit",
        "stopPrice":     float(f"{tp_price:.8f}"),
        "triggerPrice":  float(f"{tp_price:.8f}"),
        "stop":          "up"   if side_dir == "long" else "down"
    }
    sl_params = {
        "reduceOnly":    True,
        "type":          "stop_loss",
        "stopPrice":     float(f"{sl_price:.8f}"),
        "triggerPrice":  float(f"{sl_price:.8f}"),
        "stop":          "down" if side_dir == "long" else "up"
    }

    tp_ok = sl_ok = True
    try:
        ex.create_order(symbol, "market", exit_side, pos_amt, None, tp_params)
    except Exception as e:
        tp_ok = False
        tg(TG_TOKEN, TG_CHAT, f"⚠️ TP failed: {e}")
    try:
        ex.create_order(symbol, "market", exit_side, pos_amt, None, sl_params)
    except Exception as e:
        sl_ok = False
        tg(TG_TOKEN, TG_CHAT, f"⚠️ SL failed: {e}")

    tg(TG_TOKEN, TG_CHAT,
       f"✅ ENTERED {symbol} {side_dir.upper()}\n"
       f"Qty: {amount}\nEntry: {entry_price:.2f}\n"
       f"TP {tp_price:.2f} ({'OK' if tp_ok else 'ERR'}) | SL {sl_price:.2f} ({'OK' if sl_ok else 'ERR'})\n"
       f"Time: {now_str(tz)}")

def run_loop():
    load_dotenv()
    global TG_TOKEN, TG_CHAT
    TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID")

    ex       = ccxt.kucoinfutures({
        "apiKey":   os.getenv("KUCOINFUTURES_API_KEY"),
        "secret":   os.getenv("KUCOINFUTURES_SECRET"),
        "password":os.getenv("KUCOINFUTURES_PASSWORD"),
        "enableRateLimit": True,
    })

    cfg      = load_cfg()
    tz       = cfg.get("timezone", "Europe/Berlin")
    symbol   = cfg["symbol"]
    interval = int(cfg.get("interval_minutes", 30))

    tg(TG_TOKEN, TG_CHAT, f"🤖 AI ETH bot started — interval {interval}m | {now_str(tz)}")

    while True:
        try:
            positions = ex.fetch_positions([symbol])
            has_pos   = any(abs(float(p.get("contracts") or 0)) > 0 for p in positions)
            if has_pos:
                tg(TG_TOKEN, TG_CHAT, f"ℹ️ Position already open. Skip cycle. {now_str(tz)}")
            else:
                df   = fetch_candles_df(ex, symbol, cfg.get("bar_tf","5m"),
                                        limit = max(300, cfg.get("lookback_bars",200)))
                side, diag = signals.decide_direction(df.tail(cfg.get("lookback_bars",200)), cfg)
                tg(TG_TOKEN, TG_CHAT,
                   f"📊 Signal diag: proba_up={diag.get('proba_up')}, news={diag.get('news_sent')}, blended={diag.get('blended')}")

                if side in ("long","short"):
                    open_position(ex, cfg, side)
                else:
                    tg(TG_TOKEN, TG_CHAT, f"⏸️ SKIP (low confidence). {now_str(tz)}")
        except Exception as e:
            tg(TG_TOKEN, TG_CHAT, f"⚠️ Loop error: {e}")
        finally:
            time.sleep(interval * 60)

if __name__ == "__main__":
    run_loop()
