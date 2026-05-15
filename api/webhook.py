import os
import json
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None

try:
    import redis
except ImportError:
    redis = None

try:
    from groq import Groq
except ImportError:
    Groq = None
    GroqError = Exception
else:
    GroqError = Exception

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8429170788"))
REDIS_URL = os.getenv("REDIS_URL")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

STAR_PRICE_30D = 50
MIN_SUB_DAYS = 30
MAX_SUB_DAYS = 365
FREE_LIMIT = 20
FREE_COOLDOWN_SEC = 90
DAILY_CHECKIN_COOLDOWN = 86400
PREMIUM_MAX_TOKENS = 600
PREMIUM_COOLDOWN_SEC = 30
GROQ_TIMEOUT = 7

groq_client = None
r = None
mem_state = {}

def escape_md(text):
    if not text:
        return ""
    chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join('\\' + c if c in chars else c for c in str(text))

def safe_request(url, **kwargs):
    if not requests:
        print("requests not installed")
        return None
    try:
        resp = requests.post(url, timeout=8, **kwargs)
        if resp.status_code >= 400:
            print(f"Telegram API error: {resp.status_code} {resp.text}")
        return resp
    except Exception as e:
        print(f"Request failed: {e}")
        return None

def get_groq_client():
    global groq_client
    if groq_client is None and GROQ_API_KEY and Groq:
        groq_client = Groq(api_key=GROQ_API_KEY, timeout=GROQ_TIMEOUT)
    return groq_client

def alert_admin(msg):
    if not ADMIN_ID or not BOT_TOKEN or not requests:
        print(f"ADMIN ALERT: {msg}")
        return
    safe_request(f"{TELEGRAM_API}/sendMessage",
                 json={"chat_id": ADMIN_ID, "text": escape_md(str(msg))})

def init_redis():
    global r
    if r is None and REDIS_URL and redis:
        try:
            r = redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_timeout=3,
                socket_connect_timeout=3
            )
            r.ping()
        except Exception as e:
            r = None
            print(f"Redis down: {e}")
    return r

def send_message(chat_id, text, reply_markup=None, parse_mode="MarkdownV2"):
    if not BOT_TOKEN or not requests:
        print("Cannot send message: missing token or requests")
        return
    if len(text) > 4000:
        text = text[:3900] + "\n\n...truncated"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    safe_request(f"{TELEGRAM_API}/sendMessage", json=payload)

def answer_callback(callback_id):
    if not BOT_TOKEN or not requests:
        return
    safe_request(f"{TELEGRAM_API}/answerCallbackQuery",
                 json={"callback_query_id": callback_id})

def get_user(user_id, telegram_name="friend"):
    key = f"user:{user_id}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    redis_client = init_redis()

    if redis_client:
        try:
            data = redis_client.hgetall(key)
            if not data:
                data = {"name": telegram_name, "msgs_today": "0", "last_reset": today,
                        "wallet": "", "exp": "0", "last_msg": "0", "last_checkin": "0",
                        "last_depin_checkin": "0", "streak": "0"}
                redis_client.hset(key, mapping=data)
            else:
                if data.get("name")!= telegram_name:
                    redis_client.hset(key, "name", telegram_name)
                    data["name"] = telegram_name
            if data.get("last_reset")!= today:
                redis_client.hset(key, mapping={"msgs_today": "0", "last_reset": today})
                data["msgs_today"] = "0"
                data["last_reset"] = today
            return data
        except Exception as e:
            print(f"Redis hgetall failed: {e}")

    if user_id not in mem_state:
        mem_state[user_id] = {"name": telegram_name, "msgs_today": "0", "last_reset": today,
                              "wallet": "", "exp": "0", "last_msg": "0", "last_checkin": "0",
                              "last_depin_checkin": "0", "streak": "0"}
    if mem_state[user_id]["last_reset"]!= today:
        mem_state[user_id]["msgs_today"] = "0"
        mem_state[user_id]["last_reset"] = today
    return mem_state[user_id]

def update_user(user_id, field, value):
    redis_client = init_redis()
    if redis_client:
        try:
            redis_client.hset(f"user:{user_id}", field, str(value))
            return
        except Exception as e:
            print(f"Redis hset failed: {e}")
    if user_id not in mem_state:
        mem_state[user_id] = {}
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
        try:
            pipe = redis_client.pipeline()
            pipe.hset(f"user:{user_id}", "exp", new_exp)
            pipe.sadd("premium_subs", str(user_id))
            pipe.execute()
            return new_exp
        except Exception as e:
            print(f"Redis pipeline failed: {e}")

    update_user(user_id, "exp", new_exp)
    if "premium_subs" not in mem_state:
        mem_state["premium_subs"] = set()
    mem_state["premium_subs"].add(str(user_id))
    return new_exp

def ask_groq(prompt, max_tokens=400, retries=0):
    client = get_groq_client()
    if not client:
        return "GROQ_API_KEY not set or groq package missing"
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.7
            )
            return resp.choices[0].message.content
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"Groq error: {e}")
            alert_admin(f"Groq error: {e}")
            return "AI dey sleep small. Try again."
    return "AI failed."

def check_network_health():
    status = {}
    if requests:
        try:
            r = requests.get("https://api.telegram.org", timeout=5)
            status["Telegram"] = "✅ OK" if r.status_code == 200 else f"⚠️ {r.status_code}"
        except:
            status["Telegram"] = "❌ Down"
    else:
        status["Telegram"] = "❌ requests missing"

    client = get_groq_client()
    if client:
        try:
            client.models.list()
            status["Groq"] = "✅ OK"
        except:
            status["Groq"] = "❌ Down"
    else:
        status["Groq"] = "❌ No key"

    redis_client = init_redis()
    status["Redis"] = "✅ OK" if redis_client else "❌ Down"

    msg = "*Network Health Check*\n\n"
    for service, state in status.items():
        msg += f"{escape_md(service)}: {state}\n"
    msg += "\n✅ Safe to run projects." if all("✅" in v for v in status.values()) else "\n⚠️ Hold off on heavy tasks."
    return msg

def handle_message(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = msg.get("text", "").strip()
    telegram_name = msg["from"].get("first_name", "friend")
    user = get_user(user_id, telegram_name)
    now = int(time.time())
    premium = is_premium(user_id)

    if text == "/start":
        send_message(chat_id, f"Yo {escape_md(user['name'])}! Bot dey alive ✅\nUse /help to see commands")
        return

    if text == "/premium":
        if premium:
            exp = int(user.get("exp", 0))
            if user_id == ADMIN_ID:
                send_message(chat_id, "✅ You have Lifetime Premium\nNo expiry")
            else:
                remaining = max(0, exp - now)
                days = remaining // 86400
                hours = (remaining % 86400) // 3600
                mins = (remaining % 3600) // 60
                send_message(chat_id,
                    f"✅ You have Premium\n"
                    f"Expires in: {days}d {hours}h {mins}m\n"
                    f"Enjoy unlimited AI!"
                )
        else:
            markup = {"inline_keyboard": [[{"text": f"Buy 30 days - {STAR_PRICE_30D} Stars", "pay": True}]]}
            send_message(chat_id,
                "*Premium Benefits*\n"
                "✅ Unlimited /ask\n"
                "✅ 30s cooldown instead of 90s\n"
                "✅ /deep for long answers\n"
                "✅ /summarize text\n"
                f"\nPrice: {STAR_PRICE_30D} Stars for 30 days\n"
                "Pay with Telegram Stars",
                markup
            )
        return

    if text == "/help":
        help_text = (
            "*Commands*\n"
            "/ask <question> \\- Chat with AI\n"
            "/premium \\- View benefits & buy premium\n"
            "/stats \\- Your usage stats\n"
            "/checkin \\- Daily check\\-in\n"
            "/depin \\- Daily DePIN check\\-in\n"
            "/status \\- Check premium\n"
            "/network \\- Check network health\n"
        )
        if premium:
            help_text += "\n*Premium Commands*\n"
            help_text += "/deep <question> \\- Short AI answer\n"
            help_text += "/summarize <text> \\- Summarize text\n"
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
            f"Premium: {escape_md(exp_str)}\n"
            f"Status: {'Premium' if premium else 'Free'}"
        )
        return

    if text == "/status":
        if premium:
            exp = int(user.get("exp", 0))
            exp_date = "Lifetime" if user_id == ADMIN_ID else datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%d")
            send_message(chat_id, f"✅ Premium active till {escape_md(exp_date)}")
        else:
            send_message(chat_id, "❌ No active premium. Use /premium to upgrade")
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
        send_message(chat_id, f"✅ Daily check\\-in done! Streak: {streak} days 🔥")
        return

    if text == "/depin":
        last = int(user.get("last_depin_checkin", 0))
        if now - last < DAILY_CHECKIN_COOLDOWN:
            wait = DAILY_CHECKIN_COOLDOWN - (now - last)
            send_message(chat_id, f"DePIN check\\-in done already. Wait {wait//3600}h {wait%3600//60}m")
            return
        update_user(user_id, "last_depin_checkin", now)
        tips = ask_groq("Give 1 short tip for DePIN farmers today. 1 sentence.", max_tokens=80)
        send_message(chat_id, f"✅ DePIN check\\-in recorded!\n\n*Daily Tip*: {escape_md(tips)}")
        return

    if text.startswith("/deep"):
        if not premium:
            send_message(chat_id, "🔒 Premium only. Use /premium to unlock AI answers.")
            return
        prompt = text[5:].strip()
        if not prompt:
            send_message(chat_id, "Send like this: `/deep explain quantum physics`")
            return
        send_message(chat_id, "Thinking...")
        answer = ask_groq(prompt, max_tokens=PREMIUM_MAX_TOKENS, retries=0)
        send_message(chat_id, answer, parse_mode=None) # Fixed: no Markdown escaping
        return

    if text.startswith("/summarize"):
        if not premium:
            send_message(chat_id, "🔒 Premium only. Use /premium to unlock text summarization.")
            return
        content = text[11:].strip()
        if not content:
            send_message(chat_id, "Send like this: `/summarize [text]`")
            return
        if len(content) > 2000:
            send_message(chat_id, "Text too long. Max 2000 chars on free tier.")
            return
        send_message(chat_id, "Summarizing...")
        answer = ask_groq(f"Summarize in 3 bullets:\n{content}", max_tokens=200, retries=0)
        send_message(chat_id, answer, parse_mode=None) # Fixed: no Markdown escaping
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
                send_message(chat_id, "Free limit reach for today. Use /premium to upgrade.")
                return
            update_user(user_id, "msgs_today", msgs + 1)
        update_user(user_id, "last_msg", now)
        send_message(chat_id, "Thinking...")
        answer = ask_groq(prompt, max_tokens=300 if not premium else PREMIUM_MAX_TOKENS, retries=0)
        send_message(chat_id, escape_md(answer))
        return

    send_message(chat_id, "Send `/ask your question` or use /help")

def handle_precheckout(precheckout):
    if not BOT_TOKEN or not requests:
        return
    safe_request(f"{TELEGRAM_API}/answerPreCheckoutQuery",
                 json={"precheckout_query_id": precheckout["id"], "ok": True})

def handle_successful_payment(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    new_exp = add_premium_days(user_id, MIN_SUB_DAYS)
    exp_date = datetime.fromtimestamp(new_exp, tz=timezone.utc).strftime("%Y-%m-%d")
    send_message(chat_id, f"✅ Payment successful! Premium active till {escape_md(exp_date)}")

def handler(request):
    try:
        if request.method!= "POST":
            return {"statusCode": 200, "body": "OK", "headers": {"Content-Type": "text/plain"}}
        body = request.body
        if not body:
            return {"statusCode": 200, "body": "Empty", "headers": {"Content-Type": "text/plain"}}
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        try:
            update = json.loads(body)
        except Exception as e:
            alert_admin(f"Invalid JSON: {e}")
            return {"statusCode": 400, "body": "Bad Request", "headers": {"Content-Type": "text/plain"}}

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

        return {"statusCode": 200, "body": "OK", "headers": {"Content-Type": "text/plain"}}

    except Exception as e:
        print(f"Handler crash: {e}")
        alert_admin(f"Handler crash: {e}")
        return {"statusCode": 500, "body": "Error", "headers": {"Content-Type": "text/plain"}}
