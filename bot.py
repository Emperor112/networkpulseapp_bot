import os
import logging
import threading
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "NetworkPulse Bot is live 🕊️"}), 200

@app.route('/health')
def health():
    return jsonify({"status": "ok"}), 200

# Init Groq
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("NetworkPulse Bot alive 🕊️\nSend /ask your question to chat with AI.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/start - Check if bot is alive\n/help - Show this message\n/ask [question] - Ask the AI anything")

async def ask_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask what is AI?")
        return
    
    await update.message.reply_text("Thinking...")
    
    try:
        response = client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[{"role": "user", "content": question}]
        )
        answer = response.choices[0].message.content
        await update.message.reply_text(answer)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

def main():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    logger.info(f"BOT_TOKEN loaded: {bool(BOT_TOKEN)}")
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("ask", ask_handler))
    
    logger.info("NetworkPulse Bot alive 🕊️")
    application.run_polling()

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    main() 
