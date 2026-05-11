import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) # Put your Telegram ID here or in Render env vars

if not BOT_TOKEN or not GROQ_API_KEY:
    raise ValueError("Missing BOT_TOKEN or GROQ_API_KEY in environment variables")

client = Groq(api_key=GROQ_API_KEY)

# Storage - resets on restart. Use Postgres on Render for prod
conversation_history = {}
daily_count = {}
PREMIUM_USERS = set{} # Add IDs here: {842917088, 987654321}

FREE_LIMIT = 20

def is_premium(user_id):
    return user_id in PREMIUM_USERS

def get_daily_count(user_id):
    return daily_count.get(user_id, 0)

def increment_daily_count(user_id):
    daily_count[user_id] = get_daily_count(user_id) + 1

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log errors and notify admin"""
    logger.error("Exception while handling update:", exc_info=context.error)

    error_msg = f"🔥 BOT ERROR\nUser: {update.effective_user.id if update else 'N/A'}\nError: {context.error}"

    # Notify admin if set
    if ADMIN_ID!= 0:
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=error_msg[:4000])
        except:
            pass

    # Tell user something went wrong
    if update and hasattr(update, 'message') and update.message:
        await update.message.reply_text("Something broke on my end. I’ve notified the dev. Try again in 10 sec.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💬 Chat with AI", callback_data="chat"),
         InlineKeyboardButton("🌐 Network Tools", callback_data="tools")],
        [InlineKeyboardButton("⭐ Upgrade to Premium", callback_data="upgrade")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Yo, I'm NetworkPulse AI.\n"
        "Ask me anything about networking, code, or just chat.\n\n"
        "Free: 20 msgs/day\nPremium: Unlimited + advanced tools",
        reply_markup=reply_markup
    )

async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⭐ Premium = Unlimited messages + 32k memory + advanced tools\n"
        "Contact @your_username to upgrade. Manual for now.\n"
        "Your Telegram ID: " + str(update.effective_user.id)
    )

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_msg = update.message.text

    # Check limits
    if not is_premium(user_id) and get_daily_count(user_id) >= FREE_LIMIT:
        await update.message.reply_text(
            "Free limit reached 🚫\n"
            "Hit /upgrade for unlimited access."
        )
        return

    # Get history
    history = conversation_history.get(user_id, [])
    history.append({"role": "user", "content": user_msg})

    # Keep last 8 for free, 16 for premium
    max_history = 16 if is_premium(user_id) else 8
    history = history[-max_history:]

    # Choose model
    model = "llama-3.3-70b-versatile" if is_premium(user_id) else "llama-3.1-8b-instant"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are NetworkPulse AI. Helpful for network engineers and daily questions. Be direct, clear, no fluff."}
            ] + history,
            temperature=0.7,
            max_tokens=800
        )

        reply = response.choices[0].message.content
        history.append({"role": "assistant", "content": reply})
        conversation_history[user_id] = history
        increment_daily_count(user_id)

        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Groq error for user {user_id}: {e}")
        await update.message.reply_text("Groq dey sleep small. Try again now.")

async def subnet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("Usage: /subnet 192.168.1.0/24")
        return
    await update.message.reply_text("Subnet tool coming. For now ask: 'calculate subnet for 192.168.1.0/24'")

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("upgrade", upgrade))
    app.add_handler(CommandHandler("subnet", subnet_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    # Add global error handler
    app.add_error_handler(error_handler)

    logger.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
