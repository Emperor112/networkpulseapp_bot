import os, json, time, requests, threading
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEYS = [k.strip() for k in os.getenv("GROQ_KEYS", os.getenv("GROQ_KEY", "")).split(",") if k.strip()]
USDT_WALLET = os.getenv("USDT_WALLET")
AD_URL = os.getenv("AD_URL", "https://your-real-ad-link.com") # <-- put your idan/ad link here
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")
FREE_LIMIT = int(os.getenv("FREE_LIMIT", 5))
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x} # <-- no hardcoded IDs

MODELS = {"free": "llama-3.1-8b-instant", "pro": "llama-3.1-70b-versatile", "premium": "llama-3.1-70b-versatile"}
DAILY_TOKEN_LIMIT = {"free": 5000, "pro": 50000, "premium": 150000}
PLANS = {
    "pro7d": {"days": 7, "micro": 990000, "name": "Pro 7 Days", "tier": "pro"},
    "pro1m": {"days": 30, "micro": 2100000, "name": "Pro 1 Month", "tier": "pro"},
    "pro3m": {"days": 90, "micro": 5000000, "name": "Pro 3 Months", "tier": "pro"},
    "prem1m": {"days": 30, "micro": 4000000, "name": "Premium 1 Month", "tier": "premium"},
}

USERS_FILE = "users.json"
users = {}
processed_txs = set()
groq_idx = 0
save_lock = threading.Lock()

def load():
    global users, processed_txs
    try:
        with open(USERS_FILE) as f:
            data = json.load(f)
            users = {k: v for k, v in data.items() if not k.startswith("_")}
            processed_txs = set(data.get("_processed", []))
    except:
        users, processed_txs = {}, set()

def save():
    with save_lock:
        data = {**users, "_processed": list(processed_txs)}
        with open(USERS_FILE, "w") as f:
            json.dump(data, f)

def get_today():
    return time.strftime("%Y-%m-%d")

def get_user(uid, ctx=None):
    uid = str(uid)
    if uid not in users:
        users[uid] = {
            "tier": "premium" if int(uid) in ADMIN_IDS else "free",
            "expires": None,
            "blocked": False,
            "c": FREE_LIMIT,
            "b": 0,
            "ref_by": None,
            "ref_count": 0,
            "tokens": 0,
            "last_reset": get_today(),
            "username": ctx.from_user.username if ctx else None,
            "first": ctx.from_user.first_name if ctx else "User",
            "lang": "English"
        }
    u = users[uid]
    if u["last_reset"]!= get_today():
        u["tokens"] = 0
        u["last_reset"] = get_today()
        u["c"] = FREE_LIMIT + u["b"]
    return u

def use_credit(uid):
    u = get_user(uid)
    if u["tier"] == "free" and int(uid) not in ADMIN_IDS:
        u["c"] -= 1
        save()

def check_limit(uid):
    u = get_user(uid)
    if int(uid) in ADMIN_IDS or u["tier"]!= "free":
        return True, ""
    if u["c"] <= 0:
        return False, "No credits. Use /unlock for +5 or upgrade with /pay"
    return True, ""

def estimate_tokens(text):
    return len(text) // 4

async def groq_chat(uid, msg):
    global groq_idx
    u = get_user(uid)
    limit = DAILY_TOKEN_LIMIT[u["tier"]]
    if u["tokens"] >= limit:
        return f"🚫 Daily token limit reached. {u['tokens']}/{limit}. Resets at midnight UTC."

    model = MODELS[u["tier"]]
    max_tok = 120 if u["tier"] == "free" else 300 if u["tier"] == "pro" else 600
    sys = f"You are NetworkPulse_AI. Reply in {u['lang']}. Be fun, casual. "
    sys += "Basic chat only. If asked for code/photo say 'Upgrade to Pro'." if u["tier"] == "free" else "You can help with coding, math, photo analysis."

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {GROQ_KEYS[groq_idx]}"}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": sys}, {"role": "user", "content": msg}],
        "max_tokens": max_tok,
        "temperature": 0.9
    }

    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=12)
        if r.status_code in (429, 401):
            groq_idx = (groq_idx + 1) % len(GROQ_KEYS)
            return await groq_chat(uid, msg)
        reply = r.json()["choices"][0]["message"]["content"]
        u["tokens"] += estimate_tokens(msg) + estimate_tokens(reply)
        save()
        return reply
    except:
        return "NetworkPulse is catching its breath 😅 Try again in 1 min."

async def check_payment(uid, tx):
    if tx in processed_txs:
        return False, "TX already used"
    try:
        r = requests.get(f"https://apilist.tronscan.org/api/transaction-info?hash={tx}", timeout=10)
        data = r.json()
        if data.get("to") == USDT_WALLET and data.get("tokenTransfers"):
            usdt = next((t for t in data["tokenTransfers"] if t.get("tokenInfo", {}).get("symbol") == "USDT"), None)
            if not usdt:
                return False, "No USDT found"
            amount = float(usdt["amount_str"])
            plan = next((p for p in PLANS.values() if amount >= p["micro"]), None)
            if not plan:
                return False, "Amount too low. Min $0.99"
            processed_txs.add(tx)
            u = get_user(uid)
            bonus = 7 if "1 Month" in plan["name"] else 0
            u["tier"] = plan["tier"]
            u["expires"] = int(time.time() * 1000) + (plan["days"] + bonus) * 86400000
            save()
            msg = f"✅ Payment confirmed! You are now {plan['tier'].upper()} for {plan['name']}"
            if bonus:
                msg += f" + {bonus} days BONUS!"
            return True, msg
    except:
        pass
    return False, "TX not found or wrong wallet"

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = ctx.args
    u = get_user(uid, update)

    if args:
        try:
            ref_id = int(args[0])
            if ref_id!= uid and not u["ref_by"] and str(ref_id) in users:
                u["ref_by"] = ref_id
                ru = get_user(ref_id)
                ru["b"] += 5
                ru["ref_count"] += 1
                await ctx.bot.send_message(ref_id, "🎉 Someone joined via your referral! +5 bonus credits")
        except:
            pass

    if uid in ADMIN_IDS:
        return await update.message.reply_text("👑 Yo boss! NetworkPulse is online. Unlimited access.")
    await update.message.reply_text(
        f"🔥 Yo {u['first']}! I'm NetworkPulse AI\n"
        f"💬 Just type to chat\n"
        f"📊 /price btc\n"
        f"🎲 /bet for football tips\n"
        f"💰 /plans to upgrade\n"
        f"📢 /referral for your link"
    )

async def unlock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    if u["tier"]!= "free":
        return await update.message.reply_text("Pro/Premium users don’t need unlocks.")
    await update.message.reply_text(f"🔓 Watch this ad for +5 credits:\n{AD_URL}\n\nAfter watching, reply 'done'")

async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    uid = update.effective_user.id
    u = get_user(uid)

    if u["blocked"]:
        return await update.message.reply_text("🚫 You are blocked. Contact admin.")
    if msg.lower() == "done" and u["tier"] == "free":
        u["c"] += 5
        save()
        return await update.message.reply_text(f"✅ +5 credits added! Now you have {u['c']}")
    if msg.startswith("/"):
        return
    if len(msg) == 64 and all(c in "0123456789abcdef" for c in msg.lower()):
        await update.message.reply_text("🔍 Checking payment...")
        ok, reply = await check_payment(uid, msg)
        return await update.message.reply_text(reply)

    ok, err = check_limit(uid)
    if not ok:
        return await update.message.reply_text(err)
    use_credit(uid)

    await update.message.reply_text("💭 Thinking...")
    reply = await groq_chat(uid, msg)
    await update.message.reply_text(reply)

async def bet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok, err = check_limit(uid)
    if not ok:
        return await update.message.reply_text(err)
    use_credit(uid)
    q = " ".join(ctx.args) if ctx.args else "today football"
    reply = await groq_chat(uid, f"Give 3 short football bet tips for {q}. Format: Team vs Team - Tip - Reason. Under 80 words. Add: 'Gamble responsibly'.")
    await update.message.reply_text(reply)

# Admin commands only work if your ID is in ADMIN_IDS env var
async def ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    target = ctx.args[0] if ctx.args else None
    if not target or target not in users:
        return await update.message.reply_text("User not found")
    users[target]["blocked"] = True
    save()
    await update.message.reply_text(f"✅ User {target} blocked")

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    total = len([k for k in users if k.isdigit()])
    pro = len([u for u in users.values() if u["tier"] in ("pro", "premium")])
    await update.message.reply_text(f"📊 Bot Stats:\nTotal Users: {total}\nPro/Premium: {pro}\nFree: {total-pro}")

load()
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("unlock", unlock))
app.add_handler(CommandHandler("bet", bet))
app.add_handler(CommandHandler("ban", ban))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

if __name__ == "__main__":
    app.run_polling()
