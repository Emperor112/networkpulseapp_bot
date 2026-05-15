import os
import json
import time
import requests
import redis
from datetime import datetime, timezone
from groq import Groq, GroqError

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = 8429170788
REDIS_URL = os.getenv("REDIS_URL")

if not BOT_TOKEN or not GROQ_API_KEY:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or GROQ_API_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Config
STAR_PRICE_30D = 50
MIN_SUB_DAYS = 30
MAX_SUB_DAYS = 365
FREE_LIMIT = 20
FREE_COOLDOWN_SEC = 90
DAILY_CHECKIN_COOLDOWN = 86400

# Premium limits
PREMIUM_MAX_TOKENS = 1200
PREMIUM_COOLDOWN_SEC = 30

groq_client = None
r = None
mem_state = {}

def get_groq_client():
    global groq_client
    if groq_client is None:
        groq_client = Groq(api_key=GROQ_API_KEY, timeout=10)
    return groq_client

def alert_admin(msg):
    if ADMIN_ID and BOT_TOKEN:
        try:
            requests.post(f"{TELEGRAM_API}/sendMessage",
                          json={"chat_id": ADMIN_ID, "text": msg},
                          timeout=5)
        except:
            pass

def init_redis():
    global r
    if r is None and REDIS_URL:
        try:
            r = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)
            r.ping()
        except Exception as e:
            r = None
            alert_admin(f"🚨 Redis down: {e}")
    return r

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"}
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
                    "wallet": "", "exp": "0", "last_msg": "0", "last_checkin": "0",
                    "last_depin_checkin": "0", "streak": "0"}
            redis_client.hset(key, mapping=data)

        if data.get("last_reset")!= today:
            redis_client.hset(key, mapping={"msgs_today": "0", "last_reset": today})
            data["msgs_today"] = "0"
            data["last_reset"] = today
        return data
    else:
        if user_id not in mem_state:
            mem_state[user_id] = {"name": "friend", "msgs_today": "0", "last_reset": today,
                                  "wallet": "", "exp": "0", "last_msg": "0", "last_checkin": "0",
                                  "last_depin_checkin": "0", "streak": "0"}
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
    global r
    now = int(time.time())
    exp = int(get_user(user_id).get("exp", 0))
    if exp < now:
        exp = now
    new_exp = min(exp + days * 86400, now + MAX_SUB_DAYS * 86400)

    key = f"user:{user_id}"
    redis_client = init_redis()
    if redis_client:
        pipe = redis_client.pipeline()
        pipe.hset(key, "exp", new_exp)
        pipe.sadd("premium_subs", str(user_id))
        pipe.execute()
    else:
        update_user(user_id, "exp", new_exp)
        if "premium_subs" not in mem_state:
            mem_state["premium_subs"] = set()
        mem_state["premium_subs"].add(str(user_id))
    return new_exp

def ask_groq(prompt, max_tokens=500, retries=1):
    for attempt in range(retries + 1):
        try:
            client = get_groq_client()
            resp = client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.7
            )
            return resp.choices[0].message.content
        except GroqError as e:
            if attempt < retries:
                time.sleep(2)
                continue
            alert_admin(f"Groq error: {e}")
            return "AI dey sleep small. Try again."
    return "AI failed."

def check_network_health():
    status = {}
    try:
        r = requests.get("https://api.telegram.org", timeout=5)
        status["Telegram"] = "✅ OK" if r.status_code == 200 else f"⚠️ {r.status_code}"
    except:
        status["Telegram"] = "❌ Down"

    try:
        client = get_groq_client()
        client.models.list()
        status["Groq"] = "✅ OK"
    except:
        status["Groq"] = "❌ Down"

    redis_client = init_redis()
    status["Redis"] = "✅ OK" if redis_client else "❌ Down"

    msg = "*Network Health Check*\n\n"
    for service, state in status.items():
        msg += f"{service}: {state}\n"
    msg += "\n✅ Safe to run projects." if all("✅" in v for v in status.values()) else "\n⚠️ Hold off on heavy tasks."
    return msg

def handle_message(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "").strip()
    user = get_user(user_id)
    now = int(time.time())
    premium = is_premium(user_id)

    if text == "/start":
        send_message(chat_id, f"Yo {user['name']}! Bot dey alive ✅\nUse /help to see commands")
        return

    if text == "/help":
        help_text = (
            "*Commands*\n"
            "/ask <question> - Chat with AI\n"
            "/stats - Your usage stats\n"
            "/checkin - Daily check-in\n"
            "/depin - Daily DePIN check-in\n"
            "/status - Check premium\n"
            "/sub - Buy premium\n"
            "/network - Check network health\n"
        )
        if premium:
            help_text += "\n*Premium Commands*\n"
            help_text += "/deep <question> - Long form AI answer\n"
            help_text += "/summarize <text> - Summarize long text\n"
        help_text += f"\nFree tier: {FREE_LIMIT} msgs/day, {FREE_COOLDOWN_SEC}s cooldown"
        send_message(chat_id, help_text)
        return

    if text == "/stats":
        msgs = int(user.get("msgs_today", 0))
        streak = int(user.get("streak", 0))
        exp = int(user.get("exp", 0))
        exp_str = "Lifetime" if user_id == ADMIN_ID else (
            datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%d") if exp > now else "None"
        )
        send_message(chat_id,
            f"*Your Stats*\n"
            f"Messages today: {msgs}/{FREE_LIMIT}\n"
            f"Streak: {streak} days 🔥\n"
            f"Premium: {exp_str}\n"
            f"Status: {'Premium' if premium else 'Free'}"
        )
        return

    if text == "/status":
        if premium:
            exp = int(user.get("exp", 0))
            exp_date = "Lifetime" if user_id == ADMIN_ID else datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%d")
            send_message(chat_id, f"✅ Premium active till {exp_date}")
        else:
            send_message(chat_id, "❌ No active premium. Use /sub to upgrade")
        return

    if text == "/network":
        send_message(chat_id, check_network_health())
        return

    if text == "/checkin":
        last = int(user.get("last_checkin", 0))
        if now - last < DAILY_CHECKIN_COOLDOWN:
            wait = DAILY_CHECKIN_COOLDOWN - (now - last)
            send_message(chat_id, f"Already checked in. Come back in {wait//3600}h {wait%3600//60}m")
            return
        streak = int(user.get("streak", 0)) + 1
        update_user(user_id, "last_checkin", now)
        update_user(user_id, "streak", streak)
        send_message(chat_id, f"✅ Daily check-in done! Streak: {streak} days 🔥")
        return

    if text == "/depin":
        last = int(user.get("last_depin_checkin", 0))
        if now - last < DAILY_CHECKIN_COOLDOWN:
            wait = DAILY_CHECKIN_COOLDOWN - (now - last)
            send_message(chat_id, f"DePIN check-in done already. Wait {wait//3600}h {wait%3600//60}m")
            return
        update_user(user_id, "last_depin_checkin", now)
        tips = ask_groq("Give 1 short tip for DePIN farmers today. 1 sentence.", max_tokens=100)
        send_message(chat_id, f"✅ DePIN check-in recorded!\n\n*Daily Tip*: {tips}")
        return

    if text == "/sub":
        markup = {"inline_keyboard": [[{"text": f"Buy 30 days - {STAR_PRICE_30D} Stars", "pay": True}]]}
        send_message(chat_id, f"Upgrade to Premium: {STAR_PRICE_30D} Stars for 30 days\nUnlimited AI, faster cooldown", markup)
        return

    if text.startswith("/deep"):
        if not premium:
            send_message(chat_id, "🔒 Premium only. Use /sub to unlock long-form AI answers.")
            return
        prompt = text[5:].strip()
        if not prompt:
            send_message(chat_id, "Send like this: `/deep write me a 500 word essay on AI`")
            return
        send_message(chat_id, "Thinking deep...")
        answer = ask_groq(prompt, max_tokens=PREMIUM_MAX_TOKENS, retries=1)
        send_message(chat_id, answer)
        return

    if text.startswith("/summarize"):
        if not premium:
            send_message(chat_id, "🔒 Premium only. Use /sub to unlock text summarization.")
            return
        content = text[11:].strip()
        if not content:
            send_message(chat_id, "Send like this: `/summarize [long text]`")
            return
        if len(content) > 4000:
            send_message(chat_id, "Text too long. Max 4000 chars.")
            return
        send_message(chat_id, "Summarizing...")
        answer = ask_groq(f"Summarize this in 5 bullet points:\n{content}", max_tokens=400, retries=1)
        send_message(chat_id, answer)
        return

    if text.startswith("/ask"):
        prompt = text[4:].strip()
        if not prompt:
            send_message(chat_id, "Send like this: `/ask explain quantum`")
            return

        cooldown = PREMIUM_COOLDOWN_SEC if premium else FREE_COOLDOWN_SEC
        last_msg = int(user.get("last_msg", 0))
        if now - last_msg < cooldown:
            send_message(chat_id, f"Wait {cooldown - (now - last_msg)}s before next message.")
            return

        if not premium:
            msgs = int(user.get("msgs_today", 0))
            if msgs >= FREE_LIMIT:
                send_message(chat_id, "Free limit reach for today. Use /sub to upgrade.")
                return
            update_user(user_id, "msgs_today", msgs + 1)

        update_user(user_id, "last_msg", now)
        send_message(chat_id, "Thinking...")
        answer = ask_groq(prompt, max_tokens=500 if not premium else PREMIUM_MAX_TOKENS, retries=1)
        send_message(chat_id, answer)
        return

    send_message(chat_id, "Send `/ask your question` or use /help")

def handle_precheckout(precheckout):
    try:
        requests.post(f"{TELEGRAM_API}/answerPreCheckoutQuery",
                      json={"precheckout_query_id": precheckout["id"], "ok": True}, timeout=5)
    except:
        pass

def handle_successful_payment(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    new_exp = add_premium_days(user_id, MIN_SUB_DAYS)
    exp_date = datetime.fromtimestamp(new_exp, tz=timezone.utc).strftime("%Y-%m-%d")
    send_message(chat_id, f"✅ Payment successful! Premium active till {exp_date}")

def handler(request):
    if request.method!= "POST":
        return {"statusCode": 200, "body": "OK"}
    try:
        update = request.json()
    except Exception as e:
        alert_admin(f"Invalid JSON: {e}")
        return {"statusCode": 400, "body": "Bad Request"}

    try:
        if "message" in update:
            msg = update["message"]
            if "successful_payment" in msg:
                handle_successful_payment(msg)
            else:
                handle_message(msg)
        elif "callback_query" in update:
            cq = update["callback_query"]
            answer_callback(cq["id"])
        elif "pre_checkout_query" in update:
            handle_precheckout(update["pre_checkout_query"])
    except Exception as e:
        alert_admin(f"Handler error: {e}")

    return {"statusCode": 200, "body": "OK"}
