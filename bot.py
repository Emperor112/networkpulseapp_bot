import os, json, time, asyncio, aiohttp
from datetime import datetime
from collections import defaultdict
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from groq import Groq

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_KEY = os.getenv("GROQ_KEY")
USDT_WALLET = os.getenv("USDT_WALLET")
TRON_API = os.getenv("TRON_PRO_API_KEY")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x}

ADS_LINKS = [os.getenv("ADS_LINK"), os.getenv("MONETAG_LINK", "")]

# Pricing - low entry for new bot
PLANS = {"7d": 7*24*3600, "1m": 30*24*3600, "3m": 90*24*3600, "6m": 180*24*3600}
PRICES = {"7d": 1, "1m": 3, "3m": 7, "6m": 12}

# Token limits - optimized for 5k users
MAX_TOKENS = {"free": 120, "pro": 250, "premium": 350}
SPAM_COOLDOWN = 3
DAILY_MSG_LIMIT = {"free": 5, "pro": 30, "premium": 100}

groq_client = Groq(api_key=GROQ_KEY)
USERS_FILE = "users.json"
user_cache = {}
cache_lock = asyncio.Lock()
price_cache = {}
CACHE_TTL = 60
chat_history = defaultdict(list)

def get_today(): return time.strftime("%Y-%m-%d")
def get_expiry(tier):
    if tier == "premium": return 9999
    if tier.startswith("pro_"):
        try: return int(tier.split("_")[1])
        except: return 0
    return 0
def tier_key(tier): return tier.split("_")[0]

async def load_user(uid):
    uid = str(uid)
    async with cache_lock:
        if uid in user_cache: return user_cache[uid]
    try:
        with open(USERS_FILE) as f: users = json.load(f)
    except: users = {}
    if uid not in users:
        users[uid] = {"tier": "premium" if int(uid) in ADMIN_IDS else "free", "msgs_today": 0, "last_reset": get_today(), "last_msg_time": 0}
        with open(USERS_FILE, "w") as f: json.dump(users, f)
    u = users[uid]
    if u["tier"].startswith("pro_") and time.time() > get_expiry(u["tier"]):
        u["tier"] = "free"; users[uid] = u
        with open(USERS_FILE, "w") as f: json.dump(users, f)
    async with cache_lock: user_cache[uid] = u
    return u

async def save_user(uid, u):
    uid = str(uid)
    async with cache_lock:
        user_cache[uid] = u
        with open(USERS_FILE, "w") as f: json.dump(users, f)

async def check_limit(uid):
    if int(uid) in ADMIN_IDS: return True, ""
    u = await load_user(uid)
    now = time.time()
    if u["last_reset"]!= get_today():
        u["msgs_today"] = 0; u["last_reset"] = get_today()
    tk = tier_key(u["tier"])
    if u["msgs_today"] >= DAILY_MSG_LIMIT[tk]:
        return False, f"Daily limit: {DAILY_MSG_LIMIT[tk]} msgs. /upgrade to remove limits"
    if now - u["last_msg_time"] < SPAM_COOLDOWN:
        return False, f"Wait {int(SPAM_COOLDOWN - (now - u['last_msg_time']))}s"
    u["last_msg_time"] = now; u["msgs_today"] += 1
    await save_user(uid, u)
    return True, ""

async def groq_chat(uid, msg):
    u = await load_user(uid)
    tk = tier_key(u["tier"])
    # Truncate long prompts for free users to save tokens
    if tk == "free" and len(msg) > 200:
        msg = msg[:200] + "..."
    system_prompt = "You are NetworkPulse_AI. Be direct. Max 2 sentences." if tk == "free" else "You are NetworkPulse_AI. Be direct, helpful, good at coding and DePIN info."
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": msg}],
            max_tokens=MAX_TOKENS[tk], temperature=0.7
        )
        reply = resp.choices[0].message.content
        if tk == "pro":
            chat_history[uid].append(reply)
            if len(chat_history[uid]) > 5: chat_history[uid].pop(0)
        return reply
    except Exception as e:
        if "429" in str(e): return "Bot busy. Try again in 10s."
        return "Error. Try again."

async def get_price(coin_id):
    now = time.time()
    if coin_id in price_cache and now - price_cache[coin_id][1] < CACHE_TTL:
        return price_cache[coin_id][0]
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd", timeout=5) as r:
                data = await r.json()
                price = data.get(coin_id, {}).get("usd")
                if price: price_cache[coin_id] = (price, now)
                return price
        except: return None

async def get_trending():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get("https://api.coingecko.com/api/v3/search/trending", timeout=5) as r:
                data = await r.json()
                return [c["item"]["id"] for c in data["coins"][:5]]
        except: return ["bitcoin","ethereum","solana","pi-network","toncoin"]

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 NetworkPulse_AI\n"
        "/price <coin> - Crypto price\n"
        "/chat <msg> - AI Q&A + coding\n"
        "/profile - Your stats\n"
        "/faq - How to use\n"
        "/upgrade - Paid plans\n"
        "/depin - DePIN info\n"
        "Pro unlocks: /wallet, /airdrop, /history, all coins")

async def price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok, err = await check_limit(uid)
    if not ok: return await update.message.reply_text(err)
    u = await load_user(uid)
    tk = tier_key(u["tier"])
    coin_input = ctx.args[0].lower() if ctx.args else None
    if not coin_input or tk == "free":
        trending = await get_trending()
        msg = "🔥 Top 5 Trending:\n"
        for cid in trending:
            p = await get_price(cid)
            msg += f"{cid}: ${p:,.4f}\n" if p else f"{cid}: N/A\n"
        if tk == "free": msg += "\n/upgrade to check any coin"
        return await update.message.reply_text(msg)
    coin_map = {"pi": "pi-network", "btc": "bitcoin", "eth": "ethereum", "sol": "solana", "ton": "toncoin"}
    coin_id = coin_map.get(coin_input, coin_input)
    price = await get_price(coin_id)
    if price is None: await update.message.reply_text(f"Coin '{coin_input}' not found.")
    else: await update.message.reply_text(f"{coin_input.upper()}: ${price:,.6f}".rstrip('0').rstrip('.'))

async def chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok, err = await check_limit(uid)
    if not ok: return await update.message.reply_text(err)
    u = await load_user(uid)
    msg = " ".join(ctx.args) if ctx.args else update.message.text
    reply = await groq_chat(uid, msg)
    if u["tier"] == "free" and ADS_LINKS[0]:
        ad = ADS_LINKS[u["msgs_today"] % 2] if ADS_LINKS[1] else ADS_LINKS[0]
        if ad: reply += f"\n\n🔗 {ad}\n\n/upgrade to remove ads"
    await update.message.reply_text(reply)

async def profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await load_user(uid)
    tk = tier_key(u["tier"])
    left = max(0, DAILY_MSG_LIMIT[tk] - u["msgs_today"])
    expiry = get_expiry(u["tier"])
    expiry_str = "Never" if expiry == 9999 else ("Expired" if expiry == 0 else datetime.fromtimestamp(expiry).strftime("%Y-%m-%d"))
    ads = "Yes" if u["tier"] == "free" else "No"
    await update.message.reply_text(f"👤 Profile\nTier: {tk.upper()}\nMessages left: {left}/{DAILY_MSG_LIMIT[tk]}\nPlan expires: {expiry_str}\nAds: {ads}")

async def faq(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 FAQ\nQ: How to upgrade?\nA: /upgrade -> pay USDT TRC20 -> /report + send screenshot\n"
        "Q: Which USDT network?\nA: TRC20 only\nQ: When activated?\nA: Admin checks screenshot, usually <1hr\n"
        "Q: Why ads?\nA: Free tier shows ads. /upgrade to remove\nQ: What is DePIN?\nA: Earn crypto by sharing resources")

async def upgrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = "💳 Upgrade Plans - USDT TRC20 ONLY:\n\n"
    for plan, price in PRICES.items():
        days = plan.replace("d", " Days").replace("m", " Month").replace("1 Month", "1 Month").replace("3 Month", "3 Months").replace("6 Month", "6 Months")
        text += f"{plan.upper()}: {price} USDT - {days}\n"
    text += f"\nSend to:\n`{USDT_WALLET}`\n\nThen /report + send screenshot.\n⚠️ TRC20 only."
    await update.message.reply_text(text)

async def report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        return await update.message.reply_text("Send payment screenshot here after paying.")
    uid = update.effective_user.id
    for admin in ADMIN_IDS:
        try:
            await ctx.bot.forward_message(admin, update.effective_chat.id, update.message_id)
            await ctx.bot.send_message(admin, f"Payment from {uid}. Use /adduser {uid} <plan>")
        except: pass
    await update.message.reply_text("Screenshot sent. You’ll be activated once verified.")

async def depin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok, err = await check_limit(uid)
    if not ok: return await update.message.reply_text(err)
    await update.message.reply_text("📡 DePIN Projects:\nHelium - Wireless\nRender - GPU\nGrass - Bandwidth\nAkash - Cloud\nAsk me about any DePIN.")

async def wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await load_user(uid)
    if tier_key(u["tier"]) == "free":
        return await update.message.reply_text("Pro only. /upgrade to unlock wallet checker.")
    if not ctx.args: return await update.message.reply_text("Usage: /wallet <TRX_address>")
    addr = ctx.args[0]
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://api.trongrid.io/v1/accounts/{addr}", timeout=5) as r:
                data = await r.json()
                trx = int(data["data"][0].get("balance",0))/1e6
                await update.message.reply_text(f"TRX: {trx}\nUSDT TRC20: Check on Tronscan")
        except: await update.message.reply_text("Invalid address or API error")

async def airdrop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await load_user(uid)
    if tier_key(u["tier"]) == "free":
        return await update.message.reply_text("Pro only. /upgrade to unlock airdrop list.")
    await update.message.reply_text("🪂 Active DePIN Airdrops:\n1. Grass - Run node\n2. Gradient - Share bandwidth\n3. Dawn - WiFi sharing\nAsk for setup guide.")

async def history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await load_user(uid)
    if tier_key(u["tier"]) == "free":
        return await update.message.reply_text("Pro only. /upgrade to see chat history.")
    hist = chat_history.get(uid, [])
    if not hist: return await update.message.reply_text("No history yet.")
    await update.message.reply_text("\n\n---\n\n".join(hist[-5:]))

# Admin commands
async def adduser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if len(ctx.args)!= 2: return await update.message.reply_text("Usage: /adduser <user_id> <plan>")
    target_uid, plan = ctx.args
    if plan not in PLANS: return await update.message.reply_text("Invalid plan")
    u = await load_user(target_uid)
    expiry = int(time.time() + PLANS)
    u["tier"] = f"pro_{expiry}"
    await save_user(target_uid, u)
    await update.message.reply_text(f"User {target_uid} activated for {plan}")
    try: await ctx.bot.send_message(target_uid, f"✅ Pro activated for {plan}!")
    except: pass

async def extenduser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if len(ctx.args)!= 2: return await update.message.reply_text("Usage: /extenduser <user_id> <plan>")
    target_uid, plan = ctx.args
    if plan not in PLANS: return await update.message.reply_text("Invalid plan")
    u = await load_user(target_uid)
    current_expiry = get_expiry(u["tier"])
    new_expiry = max(int(time.time()), current_expiry) + PLANS
    u["tier"] = f"pro_{new_expiry}"
    await save_user(target_uid, u)
    await update.message.reply_text(f"User {target_uid} extended for {plan}")

async def removeuser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not ctx.args: return await update.message.reply_text("Usage: /removeuser <user_id>")
    target_uid = ctx.args[0]
    u = await load_user(target_uid)
    u["tier"] = "free"
    await save_user(target_uid, u)
    await update.message.reply_text(f"User {target_uid} downgraded to free")

async def listusers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    try:
        with open(USERS_FILE) as f: users = json.load(f)
    except: users = {}
    pro_count = sum(1 for u in users.values() if u["tier"]!= "free")
    await update.message.reply_text(f"Total: {len(users)}\nPro/Premium: {pro_count}\nFree: {len(users)-pro_count}")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("price", price))
app.add_handler(CommandHandler("chat", chat))
app.add_handler(CommandHandler("profile", profile))
app.add_handler(CommandHandler("faq", faq))
app.add_handler(CommandHandler("upgrade", upgrade))
app.add_handler(CommandHandler("report", report))
app.add_handler(CommandHandler("depin", depin))
app.add_handler(CommandHandler("wallet", wallet))
app.add_handler(CommandHandler("airdrop", airdrop))
app.add_handler(CommandHandler("history", history))
app.add_handler(CommandHandler("adduser", adduser))
app.add_handler(CommandHandler("extenduser", extenduser))
app.add_handler(CommandHandler("removeuser", removeuser))
app.add_handler(CommandHandler("listusers", listusers))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
app.add_handler(MessageHandler(filters.PHOTO, report))

if __name__ == "__main__":
    app.run_polling()
