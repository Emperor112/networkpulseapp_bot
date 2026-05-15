const { Telegraf } = require('telegraf');
const Redis = require('ioredis');
const Groq = require('groq-sdk');

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const GROQ_API_KEY = process.env.GROQ_API_KEY;
const ADMIN_ID = parseInt(process.env.ADMIN_ID || "8429170788");
const REDIS_URL = process.env.REDIS_URL;

if (!BOT_TOKEN) throw new Error("TELEGRAM_BOT_TOKEN not set");

const PREMIUM_PRICES = {
  '7d': { days: 7, stars: 15, label: '7 Days' },
  '30d': { days: 30, stars: 50, label: '30 Days' },
  '90d': { days: 90, stars: 120, label: '90 Days' }
};

const FREE_LIMIT = 15;
const FREE_COOLDOWN_SEC = 60;
const PREMIUM_COOLDOWN_SEC = 15;
const PREMIUM_MAX_TOKENS = 350;

const bot = new Telegraf(BOT_TOKEN);
const groq = GROQ_API_KEY ? new Groq({ apiKey: GROQ_API_KEY, timeout: 5000 }) : null;
const redis = REDIS_URL ? new Redis(REDIS_URL, { lazyConnect: true, connectTimeout: 3000 }) : null;

async function alertAdmin(error, context = "") {
  const msg = `⚠️ *Bot Error Alert*\n\n*Where:* ${context}\n*Error:* \`${escapeMd(String(error))}\``;
  try {
    await bot.telegram.sendMessage(ADMIN_ID, msg, { parse_mode: "MarkdownV2" });
  } catch (e) {
    console.log("Failed to send admin alert:", e);
  }
}

function escapeMd(text) {
  if (!text) return "";
  return String(text).replace(/[_*\[\]()~`>#+-=|{}.!]/g, '\\$&');
}

async function getUser(userId, name = "") {
  const key = `u:${userId}`;
  const today = new Date().toISOString().split('T')[0];
  
  if (!redis) return { msgs: 0, last: 0, exp: 0, reset: today, name };

  try {
    let data = await redis.hgetall(key);
    if (!Object.keys(data).length) {
      data = { msgs: 0, reset: today, last: 0, exp: 0, name };
      await redis.hset(key, data);
    }
    if (data.reset !== today) {
      await redis.hset(key, { msgs: 0, reset: today, name });
      data.msgs = 0;
      data.reset = today;
    }
    if (name && data.name !== name) {
      await redis.hset(key, 'name', name);
      data.name = name;
    }
    return {
      msgs: parseInt(data.msgs || 0),
      last: parseInt(data.last || 0),
      exp: parseInt(data.exp || 0),
      reset: data.reset,
      name: data.name || ""
    };
  } catch (e) {
    await alertAdmin(e, "getUser");
    return { msgs: 0, last: 0, exp: 0, reset: today, name };
  }
}

async function updateUser(userId, field, val) {
  if (!redis) return;
  try {
    await redis.hset(`u:${userId}`, field, String(val));
  } catch (e) {
    await alertAdmin(e, "updateUser");
  }
}

async function isPremium(userId) {
  if (userId === ADMIN_ID) return true;
  const user = await getUser(userId);
  return user.exp > Math.floor(Date.now() / 1000);
}

async function askGroq(prompt, maxTokens = 200) {
  if (!groq) return "GROQ_API_KEY not set";
  try {
    const resp = await groq.chat.completions.create({
      model: "llama3-8b-8192",
      messages: [{ role: "user", content: prompt }],
      max_tokens: maxTokens,
      temperature: 0.7
    });
    return resp.choices[0].message.content;
  } catch (e) {
    await alertAdmin(e, "askGroq");
    return "AI is slow rn. Try again.";
  }
}

// ===== COMMANDS =====

bot.start(async (ctx) => {
  try {
    const user = await getUser(ctx.from.id, ctx.from.first_name);
    await ctx.replyWithMarkdownV2(
`*Bot is live ⚡*
Welcome ${escapeMd(user.name || ctx.from.first_name)}

Use /help to see commands`
    );
  } catch (e) {
    await alertAdmin(e, "start command");
    ctx.reply("Something broke. Try again.");
  }
});

bot.help((ctx) => {
  ctx.replyWithMarkdownV2(
`*Commands*
/ask <q> \\- Ask AI
/upgrade \\- Get premium
/stats \\- Your usage
/status \\- Check premium

Free: ${FREE_LIMIT} msgs/day, 60s cooldown
Premium: Unlimited, 15s cooldown, 350 tokens`
  ).catch(e => alertAdmin(e, "help command"));
});

bot.command('ask', async (ctx) => {
  try {
    const prompt = ctx.message.text.replace('/ask', '').trim();
    if (!prompt) return ctx.reply("Use: `/ask explain quantum`");

    const user = await getUser(ctx.from.id, ctx.from.first_name);
    const premium = await isPremium(ctx.from.id);
    const cooldown = premium ? PREMIUM_COOLDOWN_SEC : FREE_COOLDOWN_SEC;
    const now = Math.floor(Date.now() / 1000);

    if (now - user.last < cooldown) {
      return ctx.reply(`Wait ${cooldown - (now - user.last)}s`);
    }
    
    if (!premium && user.msgs >= FREE_LIMIT) {
      return ctx.reply("Free limit reached. Use /upgrade to get premium");
    }

    await updateUser(ctx.from.id, 'last', now);
    if (!premium) await updateUser(ctx.from.id, 'msgs', user.msgs + 1);
    
    await ctx.reply("Thinking...");
    const answer = await askGroq(prompt, premium ? PREMIUM_MAX_TOKENS : 200);
    await ctx.reply(escapeMd(answer));
  } catch (e) {
    await alertAdmin(e, "ask command");
    ctx.reply("Error processing your request.");
  }
});

bot.command('upgrade', async (ctx) => {
  try {
    if (await isPremium(ctx.from.id)) {
      return ctx.reply("✅ You already have premium");
    }

    ctx.replyWithMarkdownV2(
`*Premium*
✅ Unlimited messages
✅ 15s cooldown vs 60s
✅ 350 tokens vs 200

Choose a plan:`,
      {
        reply_markup: {
          inline_keyboard: [
            [{ text: `7 Days - 15 Stars`, callback_data: "buy_7d" }],
            [{ text: `30 Days - 50 Stars`, callback_data: "buy_30d" }],
            [{ text: `90 Days - 120 Stars`, callback_data: "buy_90d" }]
          ]
        }
      }
    );
  } catch (e) {
    await alertAdmin(e, "upgrade command");
    ctx.reply("Error loading upgrade info.");
  }
});

bot.on('callback_query', async (ctx) => {
  try {
    const data = ctx.callbackQuery.data;
    if (!data.startsWith('buy_')) return;

    const planKey = data.replace('buy_', '');
    const plan = PREMIUM_PRICES[planKey];
    if (!plan) return ctx.answerCbQuery("Invalid plan");

    await ctx.answerCbQuery();
    await ctx.replyWithInvoice({
      chat_id: ctx.from.id,
      title: `Premium ${plan.label}`,
      description: `Unlimited messages, 15s cooldown, 350 tokens for ${plan.days} days`,
      payload: `premium_${planKey}`,
      provider_token: "",
      currency: "XTR",
      prices: [{ label: plan.label, amount: plan.stars }],
      start_parameter: `premium_${planKey}`
    });
  } catch (e) {
    await alertAdmin(e, "callback_query");
    ctx.answerCbQuery("Payment error");
  }
});

bot.command('stats', async (ctx) => {
  try {
    const user = await getUser(ctx.from.id);
    const premium = await isPremium(ctx.from.id);
    ctx.replyWithMarkdownV2(
`*Stats*
Messages today: ${user.msgs}/${FREE_LIMIT}
Status: ${premium ? 'Premium' : 'Free'}`
    );
  } catch (e) {
    await alertAdmin(e, "stats command");
    ctx.reply("Error loading stats.");
  }
});

bot.command('status', async (ctx) => {
  try {
    const premium = await isPremium(ctx.from.id);
    const user = await getUser(ctx.from.id);
    const expDate = user.exp > 0 ? new Date(user.exp * 1000).toISOString().split('T')[0] : "None";
    ctx.reply(premium ? `✅ Premium active till ${expDate}` : "❌ No active premium. Use /upgrade");
  } catch (e) {
    await alertAdmin(e, "status command");
    ctx.reply("Error checking status.");
  }
});

bot.command('giveprem', async (ctx) => {
  try {
    if (ctx.from.id !== ADMIN_ID) return ctx.reply("Not allowed");
    const parts = ctx.message.text.split(' ');
    const targetId = parseInt(parts[1]);
    const days = Math.max(1, parseInt(parts[2]) || 30);
    if (!targetId) return ctx.reply("Use: /giveprem USERID DAYS");

    const newExp = Math.floor(Date.now() / 1000) + days * 86400;
    await updateUser(targetId, 'exp', newExp);
    ctx.reply(`✅ Gave ${days}d premium to ${targetId}`);
  } catch (e) {
    await alertAdmin(e, "giveprem command");
    ctx.reply("Error giving premium.");
  }
});

bot.command('network', async (ctx) => {
  try {
    if (ctx.from.id !== ADMIN_ID) return;

    const status = {};
    try {
      await bot.telegram.getMe();
      status.Telegram = "✅ OK";
    } catch (e) {
      status.Telegram = "❌ Down";
    }

    if (groq) {
      try {
        await groq.models.list();
        status.Groq = "✅ OK";
      } catch (e) {
        status.Groq = "❌ Bad key";
      }
    } else {
      status.Groq = "❌ No key";
    }

    status.Redis = redis ? "✅ OK" : "❌ Down";

    let msg = "*Network Health*\n\n";
    for (const [s, v] of Object.entries(status)) {
      msg += `${escapeMd(s)}: ${v}\n`;
    }
    ctx.replyWithMarkdownV2(msg);
  } catch (e) {
    await alertAdmin(e, "network command");
  }
});

// ===== PAYMENTS =====
bot.on('pre_checkout_query', async (ctx) => {
  try {
    const payload = ctx.update.pre_checkout_query.invoice_payload;
    const planKey = payload.replace('premium_', '');
    if (payload.startsWith('premium_') && PREMIUM_PRICES[planKey]) {
      await ctx.answerPreCheckoutQuery(true);
    } else {
      await ctx.answerPreCheckoutQuery(false, "Invalid payment");
    }
  } catch (e) {
    await alertAdmin(e, "pre_checkout_query");
    await ctx.answerPreCheckoutQuery(false, "Error processing payment");
  }
});

bot.on('successful_payment', async (ctx) => {
  try {
    const payment = ctx.update.message.successful_payment;
    const chargeId = payment.telegram_payment_charge_id;
    
    if (redis) {
      const exists = await redis.get(`payment:${chargeId}`);
      if (exists) return ctx.reply("Payment already processed");
      await redis.setex(`payment:${chargeId}`, 86400, '1');
    }

    const planKey = payment.invoice_payload.replace('premium_', '');
    const plan = PREMIUM_PRICES[planKey];

    if (!plan || payment.total_amount !== plan.stars || payment.currency !== "XTR") {
      return ctx.reply("Payment mismatch");
    }

    const newExp = Math.floor(Date.now() / 1000) + plan.days * 86400;
    await updateUser(ctx.from.id, 'exp', newExp);
    await ctx.reply(`✅ Payment successful! Premium activated for ${plan.days} days`);
    
    await alertAdmin(`User ${ctx.from.id} bought ${plan.label} premium`, "Payment");
  } catch (e) {
    await alertAdmin(e, "successful_payment");
    ctx.reply("Payment went through but activation failed. Contact admin.");
  }
});

// ===== MISC =====
bot.on('text', async (ctx) => {
  try {
    if (!ctx.message.text.startsWith('/')) {
      ctx.reply("Send `/ask your question`");
    }
  } catch (e) {
    await alertAdmin(e, "text handler");
  }
});

// ===== VERCEL HANDLER =====
module.exports = async (req, res) => {
  if (req.method !== 'POST') return res.status(200).send('OK');
  try { 
    await bot.handleUpdate(req.body); 
  } catch (e) {
    console.log("Handler error:", e);
    await alertAdmin(e, "main handler");
  }
  res.status(200).send('OK');
};