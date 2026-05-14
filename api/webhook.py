import os
import json
import time
import requests
import redis
from datetime import datetime, timezone

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = 8429170788 # Lifetime premium
REDIS_URL = os.getenv("REDIS_URL")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not set")

# Redis setup with fallback
r = None
if REDIS_URL:
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
    except Exception as e:
        r = None
        if BOT_TOKEN and ADMIN_ID:
            try:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                              json={"chat_id": ADMIN_ID, "text": f"🚨 Redis down: {e}", "parse_mode": "Markdown"},
                              timeout=10)
            except:
                pass

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
GROQ_API = "https://api.groq.com/openai/v1/chat/completions"

# Pricing and limits
STAR_PRICE_30D = 50
MIN_SUB_DAYS = 30
MAX_SUB_DAYS = 365
FREE_LIMIT = 20
FREE_COOLDOWN_SEC = 60 # 1 min cooldown between msgs for free users on Vercel free tier

mem_state = {}

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=20)
    except Exception as e:
        alert_admin(f"send_message failed: {e}")

def answer_callback(callback_id):
    requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": callback_id}, timeout=10)

def get_user(user_id):
    key = f"user:{user_id}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if r:
        data = r.hgetall(key)
        if not data:
            data = {"name": "friend", "msgs_today": "0", "last_reset": today, "wallet": "", "exp": "0", "last_msg": "0"}
            r.hset(key, mapping=data)

        if data.get("last_reset", "")!= today:
            data["msgs_today"] = "0"
            data["last_reset"] = today
            r.hset(key, mapping=data)
        return data
    else:
        if user_id not in mem_state:
            mem_state[user_id] = {"name": "friend", "msgs_today": "0", "last_reset": today, "wallet": "", "exp": "0", "last_msg": "0"}
        if mem_state[user_id]["last_reset"]!= today:
            mem_state[user_id]["msgs_today"] = "0"
            mem_state[user_id]["last_reset"] = today
        return mem_state[user_id]

def update_user(user_id, field, value):
    key = f"user:{user_id}"
    if r:
        r.hset(key, field, str(value))
    else:
        mem_state[user_id][field] = str(value)

def is_premium(user_id):
    if user_id == ADMIN_ID:
        return True
    exp = int(get_user(user_id).get("exp", 0))
    return exp > int(time.time())

def add_premium_days(user_id, days):
    now = int(time.time())
    exp = int(get_user(user_id).get("exp", 0))
    if exp < now:
        exp = now
    new_exp = min(exp + days * 86400, now + MAX_SUB_DAYS * 86400)
    update_user(user_id, "exp", new_exp)

    # Auto add to premium subscribers list
    if r:
        r.sadd("premium_subs", str(user_id))
    else:
        if "premium_subs" not in mem_state:
            mem_state["premium_subs"] = set()
        mem_state["premium_subs"].add(str(user_id))
    return new_exp

def get_premium_subs():
    if r:
        return r.smembers("premium_subs")
    return mem_state.get("premium_subs", set())

def alert_admin(msg):
    if ADMIN_ID and BOT_TOKEN:
        try:
            requests.post(f"{TELEGRAM_API}/sendMessage",
                          json={"chat_id": ADMIN_ID, "text": f"🚨 Bot Alert:\n`{msg}`", "parse_mode": "Markdown"},
                          timeout=10)
        except:
            pass

def call_groq(messages, model, max_tokens):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    body = {"model": model, "messages": messages, "temperature": 0.7, "max_tokens": max_tokens}
    r = requests.post(GROQ_API, headers=headers, json=body, timeout=45)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def handler(request):
    if hasattr(request, 'get_json'):
        if request.method!= "POST":
            return {"statusCode": 200, "body": "ok"}
        data = request.get_json()
    else:
        data = request

    try:
        if "message" in data:
            handle_message(data["message"])
        elif "callback_query" in data:
            cb = data["callback_query"]
            answer_callback(cb["id"])
            handle_callback(cb)
        elif "pre_checkout_query" in data:
            requests.post(f"{TELEGRAM_API}/answerPreCheckoutQuery",
                          json={"pre_checkout_query_id": data["pre_checkout_query"]["id"], "ok": True})
        elif "successful_payment" in data.get("message", {}):
            handle_payment(data["message"])

        return {"statusCode": 200, "body": "ok"}
    except Exception as e:
        alert_admin(f"Handler crash: {e}")
        return {"statusCode": 500, "body": "error"}

def handle_message(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "")
    name = msg["from"].get("first_name", "friend")

    user = get_user(user_id)
    if user["name"] == "friend" and name:
        update_user(user_id, "name", name)
    user["name"] = user["name"] if user["name"]!= "friend" else name

    premium = is_premium(user_id)
    now = int(time.time())

    if text.startswith("/start"):
        kb = {
            "inline_keyboard": [
                [{"text": "💬 Chat AI", "callback_data": "chat"}],
                [{"text": "🌐 DePIN Tools", "callback_data": "depin"}],
                [{"text": "💻 Dev Help", "callback_data": "dev"}],
                [{"text": "⭐ Upgrade", "callback_data": "upgrade"}]
            ]
        }
        send_message(chat_id,
            f"Yo {user['name']}! I'm NetworkPulse 🤖\n\n"
            "**I fit help you with:**\n"
            "• Casual chat, no cap\n"
            "• DePIN farming: Grass, Nodepay, Gradient, VPS\n"
            "• Debug code, explain errors, write scripts\n"
            "• Optimize your nodes for more earnings\n"
            f"**Free**: {FREE_LIMIT} msgs/day, 8B model, 1min cooldown\n"
            "**Premium**: Unlimited, 70B model, fast reply\n"
            "Type /help for commands.",
        kb)

    elif text.startswith("/help"):
        send_message(chat_id,
            "**Commands:**\n"
            "/start - Main menu\n"
            "/help - Show commands\n"
            "/upgrade - Get premium\n"
            "/depin - DePIN tools\n"
            "/setwallet 0x123... - Save wallet\n"
            "/diagnose - Debug logs [Premium]\n"
            "/subs - Check premium count [Admin]\n\n"
            "Just drop your question and I go answer."
        )

    elif text.startswith("/subs") and user_id == ADMIN_ID:
        subs = len(get_premium_subs())
        send_message(chat_id, f"Active premium subs: {subs} 👑")

    elif text.startswith("/upgrade"):
        if premium:
            exp_ts = int(get_user(user_id)["exp"])
            exp_date = time.strftime("%Y-%m-%d", time.gmtime(exp_ts))
            send_message(chat_id, f"You're premium already 👑\nExpires: {exp_date}")
            return

        kb = {
            "inline_keyboard": [
                [{"text": "30 Days - 50 ⭐", "callback_data": "buy_30"}],
                [{"text": "90 Days - 140 ⭐", "callback_data": "buy_90"}],
                [{"text": "365 Days - 500 ⭐", "callback_data": "buy_365"}]
            ]
        }
        send_message(chat_id,
            "**Upgrade to Premium**\n\n"
            "• Unlimited messages, no cooldown\n"
            "• Llama 3.3 70B - smarter answers\n"
            "• Priority reply, no queue\n"
            "• DePIN node diagnostics\n"
            "• Dev/code debugging\n"
            "Pick a plan:", kb)

    elif text.startswith("/setwallet"):
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "Usage: `/setwallet 0x123...`")
            return
        wallet = parts[1]
        if not wallet.startswith("0x") or len(wallet)!= 42:
            send_message(chat_id, "Invalid wallet. Send valid EVM address like `0x123...abc`")
            return
        update_user(user_id, "wallet", wallet)
        send_message(chat_id, f"Wallet saved ✅\n`{wallet}`")

    elif text.startswith("/depin"):
        send_message(chat_id,
            "**DePIN Quick Help**\n\n"
            "Ask me stuff like:\n"
            "• 'Grass node offline, fix?'\n"
            "• 'How to check Nodepay earnings?'\n"
            "• 'Best VPS for Gradient?'\n"
            "Use /diagnose if you get error logs."
        )

    elif text.startswith("/diagnose"):
        if not premium:
            send_message(chat_id, "This one na premium only 👑\nUse /upgrade to unlock log debugging.")
            return
        send_message(chat_id, "Drop your error log or node output. I go debug am sharp.")

    else:
        # Cooldown + rate limit for free users
        msgs = int(user.get("msgs_today", 0))
        last_msg = int(user.get("last_msg", 0))

        if not premium:
            if msgs >= FREE_LIMIT:
                send_message(chat_id, f"You don hit {FREE_LIMIT}/{FREE_LIMIT} free msgs today 🚫\nUse /upgrade for unlimited.")
                return
            if now - last_msg < FREE_COOLDOWN_SEC:
                wait = FREE_COOLDOWN_SEC - (now - last_msg)
                send_message(chat_id, f"Chill small 😅 Wait {wait}s before next msg. Free tier cooldown.")
                return
            update_user(user_id, "msgs_today", msgs + 1)
            update_user(user_id, "last_msg", now)

        model = "llama-3.3-70b-versatile" if premium else "llama-3.1-8b-instant"
        max_tokens = 1200 if premium else 600

        system = (
            f"You are NetworkPulse, talking to {user['name']}. "
            "Be casual, direct, Naija-friendly. No corporate talk. "
            "For DePIN: give practical steps. "
            "For dev: debug code, explain errors, write clean Python/JS. "
            "If general, be a good chat buddy."
        )

        try:
            reply = call_groq([
                {"role": "system", "content": system},
                {"role": "user", "content": text}
            ], model, max_tokens)
            send_message(chat_id, reply)
        except Exception as e:
            send_message(chat_id, "My brain lagged. Try again in 10s.")
            alert_admin(f"Groq error for {user_id}: {e}")

def handle_callback(cb):
    chat_id = cb["message"]["chat"]["id"]
    user_id = cb["from"]["id"]
    data = cb["data"]

    if data == "chat":
        send_message(chat_id, "Wetin dey sup? Ask me anything.")
    elif data == "depin":
        handle_message({"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/depin"})
    elif data == "dev":
        send_message(chat_id, "Drop your code or error here. I go debug, explain, or rewrite am.")
    elif data == "upgrade":
        handle_message({"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/upgrade"})
    elif data.startswith("buy_"):
        days = int(data.split("_")[1])
        days = min(max(days, MIN_SUB_DAYS), MAX_SUB_DAYS)
        stars = int((days / 30) * STAR_PRICE_30D)
        send_invoice(chat_id, days, stars)

def send_invoice(chat_id, days, stars):
    requests.post(f"{TELEGRAM_API}/sendInvoice", json={
        "chat_id": chat_id,
        "title": f"Premium {days} Days",
        "description": f"Unlimited AI + 70B model for {days} days",
        "payload": f"premium_{days}",
        "currency": "XTR",
        "prices": [{"label": f"{days} Days Premium", "amount": stars}],
        "provider_token": ""
    })

def handle_payment(msg):
    user_id = msg["from"]["id"]
    payload = msg["successful_payment"]["invoice_payload"]

    if payload.startswith("premium_"):
        days = int(payload.split("_")[1])
        new_exp = add_premium_days(user_id, days)
        exp_date = time.strftime("%Y-%m-%d", time.gmtime(new_exp))
        send_message(user_id, f"✅ Payment successful!\nPremium active till {exp_date} 👑")
        alert_admin(f"New sub: {user_id} for {days} days")

# Local testing
if __name__ == "__main__":
    test_update = {
        "message": {
            "chat": {"id": 123456},
            "from": {"id": 123456, "first_name": "Test"},
            "text": "/start"
        }
    }
    handler(test_update)
    print("Test run complete")
