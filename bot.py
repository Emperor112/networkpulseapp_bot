import os
import sqlite3
import threading
import logging
from flask import Flask
from telegram import Update, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    PreCheckoutQueryHandler, ContextTypes, filters
)
from groq import Groq

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
STAR_PRICE = int(os.getenv("STAR_PRICE", "300")) # 300 Stars = ~$4.20

if not BOT_TOKEN or not GROQ_API_KEY:
    raise ValueError("Missing BOT_TOKEN or GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)

# --- SQLite DB ---
conn = sqlite3.connect("bot.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    is_premium INTEGER DEFAULT 0,
    msg_count INTEGER DEFAULT 0,
    last_reset TEXT DEFAULT CURRENT_DATE
)
""")
conn.commit()

def get_user(user_id):
    cur.execute("SELECT * FROM users WHERE user_id =?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return {"user_id": user_id, "is_premium": 0, "msg_count": 0}
    return {"user_id": row[0], "is_premium": row[1], "msg_count": row[2]}

def set_premium(user_id, status=1):
    cur.execute("UPDATE users SET is_premium =? WHERE user_id =?", (status, user_id))
    conn.commit()

def increment_msg(user_id):
    cur.execute("UPDATE users SET msg_count = msg_count + 1 WHERE user_id =?", (user_id,))
    conn.commit()

# --- Flask keep-alive ---
app = Flask(__name__)
@app.route('/')
def home():
    return "NetworkPulse AI alive"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💬 Chat with AI", callback_data="chat")],
        [InlineKeyboardButton("⭐ Upgrade to Premium", callback_data="upgrade")]
    ]
    await update.message.reply_text(
        "Yo, I'm NetworkPulse AI 🤝\n\n"
        "**Free**: 20 msgs/day, smart chat, basic tools\n"
        "**Premium**: Unlimited, 32k memory, real-time tools, advanced moderation\n"
        "Use /upgrade to unlock premium with Telegram Stars ✨",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = [LabeledPrice("Premium Monthly", STAR_PRICE)]
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="NetworkPulse Premium",
        description="Unlimited chat, 32k memory, real-time tools, advanced moderation",
        payload="premium_monthly",
        provider_token="",
        currency="XTR",
        prices=prices
    )

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_premium(user_id, 1)
    await update.message.reply_text("✅ Premium activated! You now have unlimited access.")

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_msg = update.message.text
    user = get_user(user_id)

    FREE_LIMIT = 20

    # Check limit
    if user["is_premium"] == 0 and user["msg_count"] >= FREE_LIMIT:
        await update.message.reply_text(
            "Free limit reached 🚫\n"
            "Hit /upgrade for unlimited access + premium features."
        )
        return

    increment_msg(user_id)

    # Model selection
    model = "llama-3.3-70b-versatile" if user["is_premium"] == 1 else "llama-3.1-8b-instant"
    max_tokens = 1200 if user["is_premium"] == 1 else 600

    # System prompt
    system_prompt = (
        "You are NetworkPulse AI. Friendly, direct, helpful for network engineers and daily life questions. "
        "No fluff. If premium, give detailed answers and use real-time context when possible."
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg}
            ],
            temperature=0.7,
            max_tokens=max_tokens
        )

        reply = response.choices[0].message.content
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Groq error: {e}")
        await update.message.reply_text("Groq is sleeping small. Try again in 10s.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception:", exc_info=context.error)
    if ADMIN_ID!= 0:
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"Bot Error: {context.error}")
        except:
            pass

def main():
    # Start Flask for UptimeRobot
    threading.Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("upgrade", upgrade))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
    app.add_error_handler(error_handler)

    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
