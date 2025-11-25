import asyncio
import os
from typing import Any

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from state_store import store

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


async def _guard(update: Update) -> bool:
    if not CHAT_ID:
        return True
    if update.effective_chat and str(update.effective_chat.id) == str(CHAT_ID):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("Unauthorized chat")
    return False


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    await update.message.reply_text("Bot control ready. Use /run, /stop, /retrain, /status or /set <key> <value>.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    st = store.get_state()
    txt = (
        f"Status: {st['bot_status']}\n"
        f"Last action: {st.get('last_action')}\n"
        f"Last retrain: {st.get('last_retrain')}\n"
        f"Indicators: {st.get('indicators')}"
    )
    await update.message.reply_text(txt)


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    store.update_state(bot_status="running", last_action="run")
    store.record_command("run", {"source": "telegram"})
    await update.message.reply_text("Bot marked as running")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    store.update_state(bot_status="stopped", last_action="stop")
    store.record_command("stop", {"source": "telegram"})
    await update.message.reply_text("Bot marked as stopped")


async def cmd_retrain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    store.update_state(last_retrain="requested", last_action="retrain")
    store.record_command("retrain", {"source": "telegram"})
    await update.message.reply_text("Retrain requested")


async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Usage: /set <indicator> <value>")
        return
    key, value_raw = args
    try:
        value: Any = float(value_raw)
    except ValueError:
        value = value_raw
    store.update_indicators({key: value})
    store.record_command("indicator_update", {key: value, "source": "telegram"})
    await update.message.reply_text(f"Updated {key} to {value}")


async def main() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("retrain", cmd_retrain))
    app.add_handler(CommandHandler("set", cmd_set))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("Telegram control bot running...")
    await app.updater.idle()


if __name__ == "__main__":
    asyncio.run(main())
