import os, time, asyncio, aiohttp, traceback, threading
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue
from flask import Flask

# ===== FLASK KEEP-ALIVE FOR RENDER FREE TIER =====
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot running"

def run_flask():
    app_flask.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
USDT_WALLET = os.getenv("USDT_WALLET")
TRON_PRO_API_KEY = os.getenv("TRON_PRO_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
CACHE_TTL = 60

# Pricing
PRICES = {
    "pro": {"7d": 0.99, "1m": 2.99, "3m": 6.99, "6m": 12.99},
    "premium": {"7d": 1.99, "1m": 4.99, "3m": 11.99, "6m": 21.99}
}

DAYS = {"7d": 7, "1m": 30, "3m": 90, "6m": 180}

# ===== DATA =====
paid_users = {}
blocked_users = set()
referrals = {}
ref_cache = {}
price_cache = {"data": None, "ts": 0}
depin_cache = {"data": None, "ts": 0}

# ===== HELPERS =====
async def get_tier(uid):
    if str(uid) == str(ADMIN_ID):
        return "admin"
    data = paid_users.get(str(uid))
    if not data:
        return "free"
    if data["exp"] <= time.time():
        return "free"
    return data["tier"]

async def is_pro(uid):
    tier = await get_tier(uid)
    return tier in ["pro", "premium", "admin"] and str(uid) not in blocked_users

async def is_premium(uid):
    tier = await get_tier(uid)
    return tier in ["premium", "admin"] and str(uid) not in blocked_users

async def days_left(uid):
    if str(uid) == str(ADMIN_ID):
        return 9999
    data = paid_users.get(str(uid))
    if not data:
        return 0
    left = data["exp"] - time.time()
    return max(0, int(left / 86400))

def get_sub_count():
    now = time.time()
    return len([u for u in paid_users.values() if u["exp"] > now])

async def get_usdt_value(tx):
    try:
        return int(tx["value"]) / 1e6
    except:
        return 0

async def get_groq_prices(prompt):
    if not GROQ_API_KEY:
        return "Groq API key not set"
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": "Return only current crypto prices and 24h % change. Format: SYMBOL: $price +x.x%"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 300
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=payload, timeout=10) as r:
                if r.status!= 200:
                    return None
                data = await r.json()
                return data["choices"][0]["message"]["content"].strip()
    except:
        return None

async def check_payments(app):
    if not TRON_PRO_API_KEY or not USDT_WALLET:
        return
    url = f"https://api.trongrid.io/v1/accounts/{USDT_WALLET}/transactions/trc20"
    params = {"limit": 20, "contract_address": TRON_USDT_CONTRACT}
    headers = {"TRON-PRO-API-KEY": TRON_PRO_API_KEY}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, params=params) as r:
                data = await r.json()
                for tx in data.get("data", []):
                    value = await get_usdt_value(tx)
                    from_addr = tx["from"]
                    for uid, pending in list(app.bot_data.get("pending_payments", {}).items()):
                        if pending["addr"] == from_addr and value >= pending["price"]:
                            days = DAYS[pending["dur"]]
                            paid_users[uid] = {
                                "tier": pending["tier"],
                                "exp": time.time() + days * 86400
                            }
                            try:
                                await app.bot.send_message(
                                    chat_id=int(uid),
                                    text=f"✅ {pending['tier'].title()} {pending['dur']} activated for {days} days."
                                )
                            except:
                                pass
                            del app.bot_data["pending_payments"][uid]
    except Exception as e:
        print(f"Payment check error: {e}")
        if ADMIN_ID:
            try:
                await app.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ Payment check error:\n{e}")
            except:
                pass

# ===== ERROR HANDLER =====
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    print(f"Error: {err}")
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ Bot error:\n```{err[:3500]}```",
                parse_mode="Markdown"
            )
        except:
            pass

# ===== USER COMMANDS =====
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid in blocked_users:
        return
    if ctx.args:
        ref = ctx.args[0]
        if ref!= uid and ref not in referrals.get(uid, []):
            referrals.setdefault(uid, []).append(ref)
            ref_cache[ref] = ref_cache.get(ref, 0) + 12
    tier = await get_tier(uid)
    await update.message.reply_text(
        f"Welcome! Tier: {tier.title()}\n"
        f"Free: /price\n"
        f"Pro/Premium: /pay to upgrade\n"
        f"Check sub: /days\n"
        f"Refer: share your ID {uid} for +12h per referral"
    )

async def price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid in blocked_users:
        return

    tier = await get_tier(uid)
    now = time.time()

    if tier == "free":
        data = "BTC: $67,234 +2.1%\nETH: $3,412 +1.8%\nSOL: $152 +3.5%\nBNB: $585 +0.9%\nXRP: $0.52 +1.2%"
        await update.message.reply_text(f"Free - Top 5:\n{data}\n\n/pay to unlock full data + DePIN")
        return

    if price_cache["data"] and now - price_cache["ts"] < CACHE_TTL:
        data = price_cache["data"]
    else:
        data = await get_groq_prices("BTC ETH SOL BNB XRP ADA DOGE AVAX MATIC LINK DOT")
        if not data:
            data = price_cache["data"] or "BTC: $67k\nETH: $3.4k\nSOL: $152\nBNB: $585\nXRP: $0.52"
        else:
            price_cache["data"] = data
            price_cache["ts"] = now

    await update.message.reply_text(f"{tier.title()} Data:\n{data}")

async def depin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid in blocked_users:
        return
    if not await is_premium(uid):
        await update.message.reply_text("DePIN tracker is Premium only. /pay to upgrade.")
        return

    now = time.time()
    if depin_cache["data"] and now - depin_cache["ts"] < CACHE_TTL:
        data = depin_cache["data"]
    else:
        data = await get_groq_prices("RENDER HNT AKT AR FIL THETA IOTX ROSE")
        if not data:
            data = depin_cache["data"] or "RENDER: $7.12\nHNT: $4.05\nAKT: $2.88\nAR: $18.45\nFIL: $3.21"
        else:
            depin_cache["data"] = data
            depin_cache["ts"] = now

    await update.message.reply_text(f"Premium DePIN:\n{data}")

async def days(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid in blocked_users:
        return
    left = await days_left(uid)
    tier = await get_tier(uid)
    if tier == "free":
        await update.message.reply_text("You're on Free tier. /pay to upgrade.")
    else:
        await update.message.reply_text(f"{tier.title()} active. {left} days left.")

async def pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid in blocked_users:
        return
    tier = await get_tier(uid)
    if tier!= "free" and tier!= "admin":
        await update.message.reply_text(f"You're {tier.title()}. Days left: {await days_left(uid)}")
        return
    if tier == "admin":
        await update.message.reply_text("You have lifetime access.")
        return

    if not ctx.args:
        msg = "Upgrade options:\n\n"
        for t in ["pro", "premium"]:
            msg += f"{t.title()}:\n"
            for d, p in PRICES[t].items():
                msg += f" {d}: ${p} USDT\n"
        msg += f"\nSend to:\n`{USDT_WALLET}`\n"
        msg += f"Then: /pay YOUR_TRON_ADDRESS pro 1m"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    if len(ctx.args) < 3:
        await update.message.reply_text("Usage: /pay YOUR_TRON_ADDRESS pro/premium 7d|1m|3m|6m")
        return

    addr, plan, dur = ctx.args[0], ctx.args[1].lower(), ctx.args[2].lower()
    if plan not in PRICES or dur not in PRICES[plan]:
        await update.message.reply_text("Invalid plan or duration. Use: pro/premium + 7d/1m/3m/6m")
        return

    price = PRICES[plan][dur]
    ctx.bot_data.setdefault("pending_payments", {})[uid] = {
        "addr": addr, "tier": plan, "dur": dur, "price": price
    }
    await update.message.reply_text(f"Pending {plan.title()} {dur}. Send {price} USDT to activate.")

# ===== ADMIN COMMANDS =====
async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!= ADMIN_ID:
        return
    sub_count = get_sub_count()
    pro_count = len([u for u in paid_users.values() if u["tier"] == "pro" and u["exp"] > time.time()])
    prem_count = len([u for u in paid_users.values() if u["tier"] == "premium" and u["exp"] > time.time()])
    blocked_count = len(blocked_users)
    await update.message.reply_text(
        f"📊 Bot Stats:\n"
        f"Active subs: {sub_count}\n"
        f"Pro: {pro_count}\n"
        f"Premium: {prem_count}\n"
        f"Blocked: {blocked_count}"
    )

async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!= ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /broadcast Your announcement message")
        return
    msg = " ".join(ctx.args)
    now = time.time()
    sent = 0
    for uid, data in paid_users.items():
        if data["exp"] > now:
            try:
                await ctx.bot.send_message(chat_id=int(uid), text=f"📢 Announcement:\n\n{msg}")
                sent += 1
            except:
                pass
    await update.message.reply_text(f"Broadcast sent to {sent} active subscribers.")

async def refstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!= ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /refstats USER_ID")
        return
    target = ctx.args[0]
    hours = ref_cache.get(target, 0)
    ref_list = referrals.get(target, [])
    await update.message.reply_text(
        f"User {target}:\n"
        f"Referral hours: {hours}h\n"
        f"Referred users: {len(ref_list)}"
    )

async def add_paid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!= ADMIN_ID:
        return
    if len(ctx.args) < 3:
        await update.message.reply_text("Usage: /add_paid USER_ID pro/premium 7d|1m|3m|6m")
        return
    uid, tier, dur = ctx.args[0], ctx.args[1].lower(), ctx.args[2].lower()
    if tier not in PRICES or dur not in PRICES[tier]:
        await update.message.reply_text("Invalid tier or duration")
        return
    days = DAYS[dur]
    paid_users[uid] = {"tier": tier, "exp": time.time() + days * 86400}
    await update.message.reply_text(f"Added {uid} to {tier.title()} {dur}")

async def remove_paid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!= ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /remove_paid USER_ID")
        return
    uid = ctx.args[0]
    if uid in paid_users:
        del paid_users[uid]
        await update.message.reply_text(f"Removed {uid} from paid users.")
    else:
        await update.message.reply_text(f"{uid} not found in paid users.")

async def block(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!= ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /block USER_ID")
        return
    blocked_users.add(ctx.args[0])
    await update.message.reply_text(f"Blocked {ctx.args[0]}")

async def unblock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!= ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /unblock USER_ID")
        return
    blocked_users.discard(ctx.args[0])
    await update.message.reply_text(f"Unblocked {ctx.args[0]}")

async def list_blocked(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!= ADMIN_ID:
        return
    if not blocked_users:
        await update.message.reply_text("No blocked users.")
    else:
        await update.message.reply_text("Blocked users:\n" + "\n".join(blocked_users))

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not set")
        return

    app = Application.builder().token(BOT_TOKEN).job_queue(JobQueue()).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("depin", depin))
    app.add_handler(CommandHandler("days", days))
    app.add_handler(CommandHandler("pay", pay))

    # Admin commands
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("refstats", refstats))
    app.add_handler(CommandHandler("add_paid", add_paid))
    app.add_handler(CommandHandler("remove_paid", remove_paid))
    app.add_handler(CommandHandler("block", block))
    app.add_handler(CommandHandler("unblock", unblock))
    app.add_handler(CommandHandler("list_blocked", list_blocked))

    # Payment checker every 30s
    app.job_queue.run_repeating(lambda ctx: check_payments(app), interval=30, first=5)

    # Start Flask so Render Web Service stays alive
    threading.Thread(target=run_flask, daemon=True).start()

    print("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
