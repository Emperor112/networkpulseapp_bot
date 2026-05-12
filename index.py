import os
import logging
import requests
import psycopg2
from flask import Flask, request
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
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
STAR_PRICE = int(os.getenv("STAR_PRICE", "300"))

if not all([BOT_TOKEN, GROQ_API_KEY, DATABASE_URL]):
    raise ValueError("Missing BOT_TOKEN, GROQ_API_KEY, or DATABASE_URL")

client = Groq(api_key=GROQ_API_KEY)

# --- Postgres DB ---
conn = psycopg2.connect(DATABASE_URL, sslmode="require")
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    is_premium INTEGER DEFAULT 0,
    msg_count INTEGER DEFAULT 0,
    last_reset DATE DEFAULT CURRENT_DATE,
    name TEXT DEFAULT NULL,
    wallet TEXT DEFAULT NULL
)
""")
conn.commit()

def get_user(user_id):
    cur.execute("SELECT * FROM users WHERE user_id =%s", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO users (user_id) VALUES (%s)", (user_id,))
        conn.commit()
        return {"user_id": user_id, "is_premium": 0, "msg_count": 0, "name": None, "wallet": None}
    return {"user_id": row[0], "is_premium": row[1], "msg_count": row[2], "name": row[4], "wallet": row[5]}

def set_premium(user_id, status=1):
    cur.execute("UPDATE users SET is_premium =%s WHERE user_id =%s", (status, user_id))
    conn.commit()

def increment_msg(user_id):
    cur.execute("UPDATE users SET msg_count = msg_count + 1 WHERE user_id =%s", (user_id,))
    conn.commit()

def set_name(user_id, name):
    cur.execute("UPDATE users SET name =%s WHERE user_id =%s", (name, user_id))
    conn.commit()

def set_wallet(user_id, wallet):
    cur.execute("UPDATE users SET wallet =%s WHERE user_id =%s", (wallet, user_id))
    conn.commit()

# --- Flask App ---
app = Flask(__name__)
application = ApplicationBuilder().token(BOT_TOKEN).build()

@app.route('/')
def home():
    return "NetworkPulse DePIN Bot alive"

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(), application.bot)
    application.update_queue.put(update)
    return "ok", 200

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    name = user["name"] or update.effective_user.first_name
    keyboard = [
        [InlineKeyboardButton("🤖 Ask DePIN AI", callback_data="chat")],
        [InlineKeyboardButton("⭐ Go Premium", callback_data="upgrade")]
    ]
    await update.message.reply_text(
        f"Yo {name}! I'm NetworkPulse, your DePIN farming buddy 🤝\n\n"
        "I help you fix nodes, check earnings, and farm smarter.\n\n"
        "**Free**: 20 msgs/day, basic AI help\n"
        "**Premium**: Unlimited, faster model, priority help\n"
        "Try: /setwallet 0x123... /node /earnings /upgrade\n"
        "Or just ask me: 'How to fix Grass error 403?'",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def name_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /name Tolu")
        return
    name = " ".join(context.args)
    set_name(update.effective_user.id, name)
    await update.message.reply_text(f"Got it {name}! I'll remember that 😎")

async def setwallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /setwallet 0x123...abc")
        return
    wallet = context.args[0]
    set_wallet(update.effective_user.id, wallet)
    await update.message.reply_text(f"Wallet saved ✅\n`{wallet}`", parse_mode="Markdown")

async def node(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Node check quick guide:\n\n"
        "**Grass**: app.getgrass.io/dashboard → check 'Connected'\n"
        "**Nodepay**: app.nodepay.ai → check extension status\n"
        "**Gradient**: app.gradient.network → check points\n"
        "Having an error? Just paste it here and I'll fix it for you."
    )

async def earnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    if not user["wallet"]:
        await update.message.reply_text("Set your wallet first with /setwallet 0x123...")
        return
    await update.message.reply_text(
        f"Checking earnings for your wallet...\n\n"
        "Auto-tracking coming soon. For now check:\n"
        "- Grass dashboard\n"
        "- Nodepay dashboard\n"
        "Want me to add live tracking for a specific project? Tell me which one."
    )

async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = [LabeledPrice("Premium Monthly", STAR_PRICE)]
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title="NetworkPulse Premium",
        description="Unlimited DePIN help, faster Llama 70B model, priority support",
        payload="premium_monthly",
        provider_token="",
        currency="XTR",
        prices=prices
    )

async def setpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only your ID 12368493 can run this
    if update.effective_user.id!= 12368493:
        await update.message.reply_text("Not for you bro 😂")
        return

    set_premium(update.effective_user.id, 1)
    await update.message.reply_text("✅ You're premium now")

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    set_premium(user_id, 1)
    await update.message.reply_text("✅ Premium activated! You got unlimited access + faster AI now.")

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_msg = update.message.text
    user = get_user(user_id)

    FREE_LIMIT = 20
    if user["is_premium"] == 0 and user["msg_count"] >= FREE_LIMIT:
        await update.message.reply_text(
            f"You've hit {FREE_LIMIT}/20 free messages today 🚫\n"
            "Hit /upgrade for unlimited help. I won't bite."
        )
        return

    increment_msg(user_id)

    model = "llama-3.3-70b-versatile" if user["is_premium"] == 1 else "llama-3.1-8b-instant"
    max_tokens = 1200 if user["is_premium"] == 1 else 600

    name = user["name"] or "farmer"
    system_prompt = (
        f"You are NetworkPulse, a friendly DePIN farming assistant talking to {name}. "
        "You're helpful, direct, and a bit casual. No corporate fluff. "
        "You specialize in Grass, Nodepay, Gradient, VPS setup, node errors, and farming tips. "
        "If asked about non-DePIN stuff, gently steer back: 'I'm built for DePIN farmers, ask me about nodes or earnings'."
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
        await update.message.reply_text("My brain lagged a bit. Try again in 10s.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception:", exc_info=context.error)
    if ADMIN_ID!= 0:
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"Bot Error: {context.error}")
        except:
            pass

# --- Register Handlers ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("name", name_cmd))
application.add_handler(CommandHandler("setwallet", setwallet))
application.add_handler(CommandHandler("node", node))
application.add_handler(CommandHandler("earnings", earnings))
application.add_handler(CommandHandler("upgrade", upgrade))
application.add_handler(CommandHandler("setpremium", setpremium)) # <-- added here
application.add_handler(PreCheckoutQueryHandler(precheckout))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
application.add_error_handler(error_handler)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
