import os
import logging
import threading
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app for Render health check
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "NetworkPulse Bot is live 🕊️"}), 200

@app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200

# Telegram Bot Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("NetworkPulse Bot alive 🕊️\nSend /help to see commands.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start - Check if bot is alive\n/help - Show this message")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"You said: {update.message.text}")

# Main function
async def main():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    logger.info("NetworkPulse Bot alive 🕊️")
    
    # Run bot polling. This blocks and runs in the main thread
    await application.run_polling()

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    # Start Flask in a background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run Telegram bot in main thread
    import asyncio
    asyncio.run(main())
