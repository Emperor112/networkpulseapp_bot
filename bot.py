import os
import logging
import threading
import nest_asyncio
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

nest_asyncio.apply()  # Fix for Render + Python 3.14

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "NetworkPulse Bot is live 🕊️"}), 200

@app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("NetworkPulse Bot alive 🕊️\nSend /help to see commands.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start - Check if bot is alive\n/help - Show this message")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"You said: {update.message.text}")

async def main():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    logger.info(f"BOT_TOKEN loaded: {bool(BOT_TOKEN)}")
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    logger.info("NetworkPulse Bot alive 🕊️")
    await application.run_polling()

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    import asyncio
    asyncio.run(main())
