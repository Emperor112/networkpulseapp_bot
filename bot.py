import os
import logging
import json
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
app_flask = Flask(__name__)

# Simple in-memory storage. Swap for Postgres later
premium_users = set()
crowd_reports = []
user_data = {}

COUNTRIES = {
    "NG": {"name": "Nigeria", "providers": ["MTN", "Airtel", "Glo", "9mobile"]},
    "KE": {"name": "Kenya", "providers": ["Safaricom", "Airtel", "Telkom"]},
    "GH": {"name": "Ghana", "providers": ["MTN", "Vodafone", "AirtelTigo"]}
}

CACHED_QA = {
    "maize yellow": "Yellow maize usually means nitrogen deficiency. Check soil pH and add urea fertilizer.",
    "node offline": "Check power, internet, and restart the node. If using DePIN, verify wallet sync."
}

@app_flask.route("/")
def home():
    return "NetworkPulse Bot alive 🕊️"

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Error:", exc_info=context.error)
    if ADMIN_ID:
        await context.bot.send_message(ADMIN_ID, f"Error: {context.error}\nUpdate: {update}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "Welcome to NetworkPulse 🌾\n"
        "1. /setcountry NG - Set your country\n"
        "2. /check - Check network status\n"
        "3. Ask any farming/DePIN question\n"
        "4. /premium - Upgrade for live AI"
    )

async def set_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.args:
        country = context.args[0].upper()
        if country in COUNTRIES:
            user_data[user_id] = {"country": country}
            await update.message.reply_text(f"Country set to {COUNTRIES[country]['name']}")
        else:
            await update.message.reply_text("Country not supported yet")
    else:
        await update.message.reply_text("Usage: /setcountry NG")

async def check_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    country = user_data.get(user_id, {}).get("country", "NG")
    providers = COUNTRIES[country]["providers"]

    # Simple crowdmap logic
    reports = [r for r in crowd_reports if r["country"] == country]
    reply = f"Network status for {COUNTRIES[country]['name']}:\n"
    for p in providers:
        down_count = sum(1 for r in reports if r["provider"] == p and r["status"] == "down")
        reply += f"{p}: {'Down' if down_count > 2 else 'Up'}\n"

    await update.message.reply_text(reply)

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) >= 2:
        provider, status = context.args[0], context.args[1]
        country = user_data.get(user_id, {}).get("country", "NG")
        crowd_reports.append({"user": user_id, "country": country, "provider": provider, "status": status})
        await update.message.reply_text("Report received. Thanks for helping the community.")
    else:
        await update.message.reply_text("Usage: /report MTN down")

async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in premium_users:
        await update.message.reply_text("You are premium. Live AI + reports unlocked.")
    else:
        await update.message.reply_text(
            "Premium $5/month:\n"
            "- Live AI answers with Claude\n"
            "- Downtime PDF reports\n"
            "- Priority alerts\n"
            "Contact @admin to activate"
        )

async def handle_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    question = update.message.text.lower()

    # Check cached first
    for key, answer in CACHED_QA.items():
        if key in question:
            await update.message.reply_text(answer)
            return

    # If premium and Claude key exists, call Claude
    if user_id in premium_users and CLAUDE_API_KEY:
        await update.message.reply_text("Live AI response coming next update. For now, cached answer only.")
    else:
        await update.message.reply_text("I don’t have an answer for that yet. Upgrade to premium for live AI.")

def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setcountry", set_country))
    app.add_handler(CommandHandler("check", check_network))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("premium", premium))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_question))
    app.add_error_handler(error_handler)
    app.run_polling()

if __name__ == "__main__":
    Thread(target=run_bot).start()
    port = int(os.getenv("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)
