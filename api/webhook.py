import os
import json
import time
import requests
import redis
from datetime import datetime, timezone
from groq import Groq, GroqError

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = 8429170788
REDIS_URL = os.getenv("REDIS_URL")

if not BOT_TOKEN or not GROQ_API_KEY:
    raise ValueError("Missing BOT_TOKEN or GROQ_API_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Config for free tier
STAR_PRICE_30D = 50
MIN_SUB_DAYS = 30
MAX_SUB_DAYS = 365
FREE_LIMIT = 20
FREE_COOLDOWN_SEC = 90 # 90s cooldown to reduce load

groq_client = None
r = None
mem_state = {}

def get_groq_client():
    global groq_client
    if groq_client is None:
        groq_client = Groq(api_key=GROQ_API_KEY)
    return groq_client

def init_redis():
    global r
    if r is None and REDIS_URL:
        try:
            r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)
            r.ping()
        except Exception as e:
            r = None
            if ADMIN_ID:
                try:
                    requests.post(f"{TELEGRAM_API}/sendMessage",
                                  json={"chat_id": ADMIN_ID, "text": f"🚨 Redis down: {e}"},
                                  timeout=5)
                except:
                    pass
    return r

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        alert_admin(f"send_message failed: {e}")

def answer_callback(callback_id):
    try:
        requests.post(f"{TELEGRAM_API}/answerCallbackQuery",
                      json={"callback_query_id": callback_id}, timeout=5)
    except:
        pass

def get_user(user_id):
    key = f"user:{user_id}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    redis_client = init_redis()

    if redis_client:
        data = redis_client.hgetall(key)
        if not data:
            data = {"name": "friend", "msgs_today": "0", "last_reset": today,
                    "wallet": "", "exp": "0", "last_msg": "0"}
            redis_client.hset(key, mapping=data)

        if data.get("last_reset")!= today:
            redis_client.hmset(key, {"msgs_today": "0", "last_reset": today})
            data["msgs_today"] = "0"
            data["last_reset"] = today
        return data
    else:
        if user_id not in mem_state:
            mem_state[user_id] = {"name": "friend", "msgs_today": "0", "last_reset": today,
                                  "wallet": "", "exp": "0", "last_msg": "0"}
        if mem_state[user_id]["last_reset"]!= today:
            mem_state[user_id]["msgs_today"] = "0"
            mem_state[user_id]["last_reset"] = today
        return mem_state[user_id]

def update_user(user_id, field, value):
    key = f"user:{user_id}"
    redis_client = init_redis()
    if redis_client:
        redis_client.hset(key, field, str(value))
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

    redis_client = init_redis()
    if redis_client:
        pipe = redis_client.pipeline()
        pipe.hset(key := f"user:{user_id}", "exp", new_exp)
        pipe.sadd("premium_subs", str(user_id))
        pipe.execute()
    else:
        update_user(user_id, "exp", new_exp)
        if "premium_subs" not in mem_state:
            mem_state["premium_subs"] = set()
        mem_state["premium_subs"].add(str(user_id))
    return new_exp

def get_premium_subs():
    redis_client = init_redis()
    if redis_client:
        return redis_client.smembers("premium_subs")
    return mem_state.get("premium_subs", set())

def alert_admin(msg):
    if ADMIN_ID and BOT_TOKEN:
        try:
            requests.post(f"{TELEGRAM_API}/sendMessage",
                          json={"chat_id": ADMIN_ID, "text": f"🚨 {msg}"},
                          timeout=5)
        except:
            pass

def call_groq(messages, model, max_tokens):
    try:
        client = get_groq_client()
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=max_tokens,
            timeout=30
        )
        return resp.choices[0].message.content
    except GroqError as e:
        alert_admin(f"Groq error: {e}")
        raise

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
        user["name"] = name

    premium = is_premium(user_id)
    now = int(time.time())

    if text.startswith("/start"):
        kb = {"inline_keyboard": [
            [{"text": "💬 Chat AI", "callback_data": "chat"}],
            [{"text": "🌐 DePIN Tools", "callback_data": "depin"}],
            [{"text": "💻 Dev Help", "callback_data": "dev"}],
            [{"text": "⭐ Upgrade", "callback_data": "upgrade"}]
        ]}
        send_message(chat_id,
            f"Yo {user['name']}! I'm NetworkPulse 🤖\n\n"
            "**I fit help you with:**\n"
            "• DePIN farming: Grass, Nodepay, Gradient, VPS\n"
            "• Debug code, explain errors\n"
            "• Optimize nodes for more earnings\n"
            f"**Free**: {FREE_LIMIT} msgs/day, 90s cooldown\n"
            "**Premium**: Unlimited, 70B model, fast reply",
        kb)

    elif text.startswith("/help"):
        send_message(chat_id,
            "**Commands:**\n"
            "/start - Main menu\n"
            "/help - Show commands\n"
            "/upgrade - Get premium\n"
            "/depin - DePIN tools\n"
            "/setwallet 0x123... - Save wallet\n"
            "/diagnose - Debug logs [Premium]"
        )

    elif text.startswith("/subs") and user_id == ADMIN_ID:
        subs = len(get_premium_subs())
        send_message(chat_id, f"Active premium subs: {subs} 👑")

    elif text.startswith("/upgrade"):
        if premium:
            exp_date = time.strftime("%Y-%m-%d", time.gmtime(int(user["exp"])))
            send_message(chat_id, f"You're premium already 👑\nExpires: {exp_date}")
            return
        kb = {"inline_keyboard": [
            [{"text": "30 Days - 50 ⭐", "callback_data": "buy_30"}],
            [{"text": "90 Days - 140 ⭐", "callback_data": "buy_90"}],
            [{"text": "365 Days - 500 ⭐", "callback_data": "buy_365"}]
        ]}
        send_message(chat_id, "**Upgrade to Premium**\nUnlimited msgs, 70B model, no cooldown\nPick a plan:", kb)

    elif text.startswith("/setwallet"):
        parts = text.split()
        if len(parts) < 2:
            send_message(chat_id, "Usage: `/setwallet 0x123...`")
            return
        wallet = parts[1]
        if not (wallet.startswith("0x") and len(wallet) == 42):
            send_message(chat_id, "Invalid wallet. Send valid EVM address")
            return
        update_user(user_id, "wallet", wallet)
        send_message(chat_id, f"Wallet saved ✅\n`{wallet}`")

    elif text.startswith("/depin"):
        send_message(chat_id,
            "**DePIN Quick Help**\n"
            "Ask: 'Grass node offline fix?'\n"
            "Ask: 'How to check Nodepay earnings?'\n"
            "Use /diagnose for error logs [Premium]"
        )

    elif text.startswith("/diagnose"):
        if not premium:
            send_message(chat_id, "Premium only 👑\nUse /upgrade")
            return
        send_message(chat_id, "Drop your error log. I go debug am sharp.")

    else:
        msgs = int(user.get("msgs_today", 0))
        last_msg = int(user.get("last_msg", 0))

        if not premium:
            if msgs >= FREE_LIMIT:
                send_message(chat_id, f"Hit {FREE_LIMIT}/{FREE_LIMIT} free msgs today 🚫\nUse /upgrade")
                return
            if now - last_msg < FREE_COOLDOWN_SEC:
                wait = FREE_COOLDOWN_SEC - (now - last_msg)
                send_message(chat_id, f"Wait {wait}s before next msg")
                return

            redis_client = init_redis()
            if redis_client:
                pipe = redis_client.pipeline()
                pipe.hincrby(f"user:{user_id}", "msgs_today", 1)
                pipe.hset(f"user:{user_id}", "last_msg", now)
                pipe.execute()
            else:
                mem_state[user_id]["msgs_today"] = str(msgs + 1)
                mem_state[user_id]["last_msg"] = str(now)

        model = "llama-3.3-70b-versatile" if premium else "llama-3.1-8b-instant"
        max_tokens = 1000 if premium else 500

        system = f"You are NetworkPulse talking to {user['name']}. Be casual, direct, Naija-friendly. No fluff."

        try:
            reply = call_groq([
                {"role": "system", "content": system},
                {"role": "user", "content": text}
            ], model, max_tokens)
            send_message(chat_id, reply)
        except Exception:
            send_message(chat_id, "My brain lagged. Try again in 10s.")

def handle_callback(cb):
    chat_id = cb["message"]["chat"]["id"]
    user_id = cb["from"]["id"]
    data = cb["data"]

    if data == "chat":
        send_message(chat_id, "Wetin dey sup? Ask me anything.")
    elif data == "depin":
        handle_message({"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/depin"})
    elif data == "dev":
        send_message(chat_id, "Drop your code or error here. I go debug am.")
    elif data == "upgrade":
        handle_message({"chat": {"id": chat_id}, "from": {"id": user_id}, "text": "/upgrade"})
    elif data.startswith("buy_"):
        days = min(max(int(data.split("_")[1]), MIN_SUB_DAYS), MAX_SUB_DAYS)
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
        send_message(user_id, f"✅ Payment successful!\nPremium till {exp_date} 👑")
        alert_admin(f"New sub: {user_id} for {days} days")
