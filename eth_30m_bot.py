import os, time, math, requests, pytz
from datetime import datetime
from decimal import Decimal
from dotenv import load_dotenv
import ccxt
import yaml

# -------- Utils --------
def now_str(tz="Europe/Berlin"):
    import pytz
    return datetime.now(pytz.timezone(tz)).strftime("%Y-%m-%d %H:%M:%S %Z")

def tg(token, chat_id, msg):
    if not token or not chat_id:
        print("[TG disabled]", msg); return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print("Telegram error:", e)

def load_cfg(path="config_eth.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def side_to_order(side):
    side = side.lower()
    if side == "long":  return "buy"
    if side == "short": return "sell"
    raise ValueError("side must be long/short")

def compute_amount(symbol_price, notional_usdt, market_precision):
    # amount (ETH) ≈ notional / price
    amt = notional_usdt / symbol_price
    prec = market_precision.get("amount", 6) if market_precision else 6
    return float(round(amt, prec))

def apply_leverage_margin(ex, symbol, lev, mode):
    try:
        ex.setLeverage(int(lev), symbol, {"marginMode": mode})
    except Exception:
        try:
            ex.setMarginMode(mode, symbol)
            ex.setLeverage(int(lev), symbol)
        except Exception as e:
            print("Leverage/margin warning:", e)

def fetch_last(ex, symbol):
    return float(ex.fetch_ticker(symbol)["last"])

def get_open_position_size(ex, symbol):
    try:
        poss = ex.fetch_positions([symbol])
        for p in poss:
            if p.get("symbol") == symbol and float(p.get("contracts", 0) or 0) != 0:
                return abs(float(p.get("contracts")))
    except Exception:
        pass
    return 0.0

# -------- Main trade flow --------
def place_entry_with_tpsl(ex, cfg, tg_token, tg_chat):
    tz = cfg.get("timezone", "Europe/Berlin")
    symbol   = cfg["symbol"]
    side_dir = cfg["side"].lower()      # long/short
    order_side = side_to_order(side_dir) # buy/sell
    lev      = int(cfg["leverage"])
    mode     = cfg.get("margin_mode", "isolated")
    entry_ty = cfg.get("entry_type", "market")
    margin   = float(cfg["margin_usdt"])
    notional = margin * lev
    tp_pct   = float(cfg["tp_percent"]) / 100.0
    sl_pct   = float(cfg["sl_percent"]) / 100.0
    dry      = bool(cfg.get("dry_run", False))

    # prepare market info
    market = ex.market(symbol)
    precision = market.get("precision", {"amount": 6, "price": 2})

    # last price (for preview msg and amount sizing)
    last = fetch_last(ex, symbol)
    amount = compute_amount(last, notional, precision)

    # Set leverage/margin
    apply_leverage_margin(ex, symbol, lev, mode)

    # Preview & notify BEFORE entry
    # compute hypothetical TP/SL from last (just preview):
    if side_dir == "long":
        tp_preview = last * (1 + tp_pct)
        sl_preview = last * (1 - sl_pct)
    else:
        tp_preview = last * (1 - tp_pct)
        sl_preview = last * (1 + sl_pct)

    tg(tg_token, tg_chat,
       f"📣 <b>Plan</b>\n"
       f"{symbol} {side_dir.upper()} {amount} (lev {lev}x, {mode})\n"
       f"Entry: {entry_ty.upper()} ~ <b>{last:.2f}</b>\n"
       f"TP ≈ {tp_preview:.2f} | SL ≈ {sl_preview:.2f}\n"
       f"Notional≈ {notional:.2f} USDT (Margin {margin}×{lev})\n"
       f"Time: {now_str(tz)}")

    if dry:
        tg(tg_token, tg_chat, "🧪 DRY-RUN: skipping real order.")
        return

    # --- Place market/limit entry (we use market as per config) ---
    if entry_ty == "market":
        order = ex.create_order(symbol, "market", order_side, amount, None, {"reduceOnly": False})
        # Try to read executed price
        entry_price = None
        try:
            # Some exchanges return average price in order['info']
            entry_price = float(order.get("price") or order.get("average") or fetch_last(ex, symbol))
        except Exception:
            entry_price = fetch_last(ex, symbol)
    else:
        # For limit, if user wants, you can add 'entry_price' in cfg
        px = float(cfg.get("entry_price", last))
        order = ex.create_order(symbol, "limit", order_side, amount, px, {"reduceOnly": False})
        entry_price = px  # will be exact if filled; if pending, TP/SL will be placed after fill in a more advanced version.

    # Compute TP/SL OFF the executed/entry price:
    if side_dir == "long":
        tp_price = entry_price * (1 + tp_pct)
        sl_price = entry_price * (1 - sl_pct)
        exit_side = "sell"
        tp_stop_dir = "up"
        sl_stop_dir = "down"
    else:
        tp_price = entry_price * (1 - tp_pct)
        sl_price = entry_price * (1 + sl_pct)
        exit_side = "buy"
        tp_stop_dir = "down"
        sl_stop_dir = "up"

    # TP/SL as conditional reduce-only orders
    # amount for exits = full position
    pos_amt = amount
    params_tp = {
        "reduceOnly": True,
        "type": "take_profit",
        "stopPrice": float(f"{tp_price:.8f}"),
        "triggerPrice": float(f"{tp_price:.8f}"),
        "stop": tp_stop_dir,  # KuCoin-specific dir hint
    }
    params_sl = {
        "reduceOnly": True,
        "type": "stop_loss",
        "stopPrice": float(f"{sl_price:.8f}"),
        "triggerPrice": float(f"{sl_price:.8f}"),
        "stop": sl_stop_dir,
    }

    tp_ok = sl_ok = True
    try:
        ex.create_order(symbol, "market", exit_side, pos_amt, None, params_tp)
    except Exception as e:
        tp_ok = False
        tg(tg_token, tg_chat, f"⚠️ TP place failed: {e}")

    try:
        ex.create_order(symbol, "market", exit_side, pos_amt, None, params_sl)
    except Exception as e:
        sl_ok = False
        tg(tg_token, tg_chat, f"⚠️ SL place failed: {e}")

    tg(tg_token, tg_chat,
       f"✅ <b>ENTERED</b> {symbol} {side_dir.upper()}\n"
       f"Qty: {amount}\n"
       f"Entry: <b>{entry_price:.2f}</b>\n"
       f"TP: {tp_price:.2f} ({'OK' if tp_ok else 'ERR'})\n"
       f"SL: {sl_price:.2f} ({'OK' if sl_ok else 'ERR'})\n"
       f"Time: {now_str(tz)}")

def run_loop():
    load_dotenv()
    TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID")
    KU_API   = os.getenv("KUCOINFUTURES_API_KEY")
    KU_SEC   = os.getenv("KUCOINFUTURES_SECRET")
    KU_PASS  = os.getenv("KUCOINFUTURES_PASSWORD")

    cfg = load_cfg()
    tz = cfg.get("timezone", "Europe/Berlin")
    symbol = cfg["symbol"]

    ex = ccxt.kucoinfutures({
        "apiKey": KU_API,
        "secret": KU_SEC,
        "password": KU_PASS,
        "enableRateLimit": True,
    })

    interval = int(cfg.get("interval_minutes", 30))
    tg(TG_TOKEN, TG_CHAT, f"🤖 ETH bot started — interval {interval}m | {now_str(tz)}")

    while True:
        try:
            # إذا في صفقة مفتوحة لنفس الرمز، ما نفتح جديدة
            open_size = get_open_position_size(ex, symbol)
            if open_size > 0:
                tg(TG_TOKEN, TG_CHAT, f"ℹ️ Position already open ({symbol}). Skip this round. {now_str(tz)}")
            else:
                place_entry_with_tpsl(ex, cfg, TG_TOKEN, TG_CHAT)
        except Exception as e:
            tg(TG_TOKEN, TG_CHAT, f"⚠️ Loop error: {e}")
        finally:
            time.sleep(interval * 60)

if __name__ == "__main__":
    from dotenv import load_dotenv
    run_loop()
