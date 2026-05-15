const { Telegraf } = require('telegraf');
const Redis = require('ioredis');
const Groq = require('groq-sdk');

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const GROQ_API_KEY = process.env.GROQ_API_KEY;
const ADMIN_ID = 8429170788;
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

function escapeMd(text) {
  if (!text) return "";
  return String(text).replace(/[_*\[\]()~`>#+-=|{}.!]/g, '\\$&');
}

async function alertAdmin(error, context = "") {
  const msg = `⚠️ *Bot Error Alert*\n\n*Where:* ${context}\n*Error:* \`${escapeMd(String(error))}\``;
  try {
    await bot.telegram.sendMessage(ADMIN_ID, msg, { parse_mode: "MarkdownV2" });
  } catch (e) {
    console.log("Failed to send admin alert:", e);
  }
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
  if (!groq) return "Set GROQ_API_KEY to use this feature";
  try {
    const systemPrompt = `You are DevBuddy, a friendly coding mentor. 
    Explain like you're helping a friend. Give working code, break down steps, 
    give practical solutions. Be clear, not robotic.`;
    
    const resp = await groq.chat.completions.create({
      model: "llama3-8b-8192",
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: prompt }
      ],
      max_tokens: maxTokens,
      temperature: 0.8
    });
    return resp.choices[0].message.content;
  } catch (e) {
    await alertAdmin(e, "askGroq");
    return "AI is slow rn. Try again.";
  }
}

const DEPIN_NODES = [
  { name: "Grass", reward: "$GRASS", apy: "20-40%" },
  { name: "Hivemapper", reward: "$HONEY", apy: "15-30%" },
  { name: "WeatherXM", reward: "$WXM", apy: "25-50%" },
  { name: "Helium", reward: "$HNT", apy: "10-25%" },
  { name: "Silencio", reward: "$NOISE", apy: "30-60%" },
  { name: "Dawn", reward: "$DAWN", apy: "40-80%" },
  { name: "Pipe Network", reward: "$PIPE", apy: "20-45%" },
  { name: "3DOS", reward: "$3DOS", apy: "15-35%" },
  { name: "Kaisar", reward: "$KAISAR", apy: "25-55%" },
  { name: "Nodepay", reward: "$NODE", apy: "30-70%" }
];

const DEPIN_AIRDROPS = [
  { name: "Grass", status: "Phase 2 Live", est: "$50-200" },
  { name: "Dawn", status: "Testnet", est: "$100-500" },
  { name: "Nodepay", status: "Season 2", est: "$80-300" },
  { name: "Kaisar", status: "Active", est: "$40-150" },
  { name: "Pipe Network", status: "Testnet", est: "$60-250" }
];

bot.start(async (ctx) => {
  try {
    const user = await getUser(ctx.from.id, ctx.from.first_name);
    await ctx.replyWithMarkdownV2(
`Yo ${escapeMd(user.name || ctx.from.first_name)} 👋

I'm DevBuddy\\. Stuck on code\\? Need a guide\\? Just ask\\. 

Try:
/dev build a login system in Node
/ask explain async await simply

I'm here to help, not judge 😎`
    );
  } catch (e) {
    await alertAdmin(e, "start command");
    ctx.reply("Something broke. Try again.");
  }
});

bot.help((ctx) => {
  ctx.replyWithMarkdownV2(
`*Hey I'm DevBuddy 🤖*
Your friendly dev guide

*Commands*
/ask <q> \\- Ask me anything
/dev <topic> \\- Get dev guides & solutions
/nodes \\- Top DEPIN projects to farm
/airdrop \\- Active DEPIN airdrops
/farm <topic> \\- Farming tips
/upgrade \\- Get premium
/stats \\- Your usage
/status \\- Check premium
/id \\- Show your ID
/feedback <msg> \\- Send feedback

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
      return ctx.reply(`Wait ${cooldown - (now - user.last)}s before next request`);
    }
    if (!premium && user.msgs >= FREE_LIMIT) {
      return ctx.reply("Free limit reached. Use /upgrade for unlimited");
    }

    await updateUser(ctx.from.id, 'last', now);
    if (!premium) await updateUser(ctx.from.id, 'msgs', user.msgs + 1);

    await ctx.reply("Cooking it up...");
    const answer = await askGroq(prompt, premium ? PREMIUM_MAX_TOKENS : 200);
    await ctx.reply(escapeMd(answer));
  } catch (e) {
    await alertAdmin(e, "ask command");
    ctx.reply("Error processing request.");
  }
});

bot.command('dev', async (ctx) => {
  try {
    const topic = ctx.message.text.replace('/dev', '').trim();
    if (!topic) return ctx.reply("Use: `/dev how to make a REST API in Node`");
    
    const premium = await isPremium(ctx.from.id);
    const prompt = premium
      ? `Give a complete step-by-step guide to ${topic}. Include code, setup, and common errors.`
      : `Give a quick solution and explanation for ${topic}.`;

    await ctx.reply("Cooking it up...");
    const answer = await askGroq(prompt, premium ? 350 : 200);
    ctx.reply(escapeMd(answer));
  } catch (e) {
    await alertAdmin(e, "dev command");
    ctx.reply("Error processing request.");
  }
});

bot.command('nodes', async (ctx) => {
  try {
    const premium = await isPremium(ctx.from.id);
    const list = premium ? DEPIN_NODES : DEPIN_NODES.slice(0, 3);
    let msg = "*Top DEPIN Nodes*\n\n";
    list.forEach((n, i) => {
      msg += `${i+1}\\. *${escapeMd(n.name)}*\n   Reward: ${escapeMd(n.reward)} | APY: ${escapeMd(n.apy)}\n`;
    });
    if (!premium) msg += "\nUpgrade with /upgrade for 10 nodes";
    ctx.replyWithMarkdownV2(msg);
  } catch (e) {
    await alertAdmin(e, "nodes command");
    ctx.reply("Error loading nodes.");
  }
});

bot.command('airdrop', async (ctx) => {
  try {
    const premium = await isPremium(ctx.from.id);
    const list = premium ? DEPIN_AIRDROPS : DEPIN_AIRDROPS.slice(0, 2);
    let msg = "*Active DEPIN Airdrops*\n\n";
    list.forEach((a, i) => {
      msg += `${i+1}\\. *${escapeMd(a.name)}*\n   Status: ${escapeMd(a.status)}${premium ? ` | Est: ${escapeMd(a.est)}` : ''}\n`;
    });
    if (!premium) msg += "\nUpgrade with /upgrade for full list + estimated value";
    ctx.replyWithMarkdownV2(msg);
  } catch (e) {
    await alertAdmin(e, "airdrop command");
    ctx.reply("Error loading airdrops.");
  }
});

bot.command('farm', async (ctx) => {
  try {
    const topic = ctx.message.text.replace('/farm', '').trim();
    if (!topic) return ctx.reply("Use: `/farm grass token`");
    
    const premium = await isPremium(ctx.from.id);
    const prompt = premium 
      ? `Give detailed farming strategy for ${topic}. Include setup, costs, expected ROI.`
      : `Give 1 quick farming tip for ${topic}`;

    await ctx.reply("Generating tip...");
    const answer = await askGroq(prompt, premium ? 350 : 150);
    ctx.reply(escapeMd(answer));
  } catch (e) {
    await alertAdmin(e, "farm command");
    ctx.reply("Error processing farm tip.");
  }
});

bot.command('id', (ctx) => {
  ctx.reply(`Your ID: ${ctx.from.id}`);
});

bot.command('feedback', async (ctx) => {
  try {
    const msg = ctx.message.text.replace('/feedback', '').trim();
    if (!msg) return ctx.reply("Use: `/feedback your message`");
    await bot.telegram.sendMessage(ADMIN_ID, `Feedback from ${ctx.from.id}:\n${msg}`);
    ctx.reply("Feedback sent. Thanks!");
  } catch (e) {
    await alertAdmin(e, "feedback command");
    ctx.reply("Error sending feedback.");
  }
});

bot.command('stats', async (ctx) => {
  try {
    const user = await getUser(ctx.from.id);
    const premium = await isPremium(ctx.from.id);
    const status = premium ? "Premium" : "Free";
    const remaining = premium ? "Unlimited" : `${FREE_LIMIT - user.msgs}`;
    ctx.reply(`*Your Stats*\nStatus: ${status}\nMessages left today: ${remaining}`, { parse_mode: "Markdown" });
  } catch (e) {
    await alertAdmin(e, "stats command");
    ctx.reply("Error loading stats.");
  }
});

bot.command('status', async (ctx) => {
  try {
    const premium = await isPremium(ctx.from.id);
    const user = await getUser(ctx.from.id);
    if (premium) {
      const expDate = new Date(user.exp * 1000).toISOString().split('T')[0];
      ctx.reply(`Status: Premium\nExpires: ${expDate}`);
    } else {
      ctx.reply("Status: Free\nUse /upgrade to get premium");
    }
  } catch (e) {
    await alertAdmin(e, "status command");
    ctx.reply("Error checking status.");
  }
});

bot.command('upgrade', async (ctx) => {
  try {
    const buttons = Object.entries(PREMIUM_PRICES).map(([key, val]) => 
      [{ text: `${val.label} - ${val.stars} Stars`, callback_data: `buy_${key}` }]
    );
    await ctx.reply("Choose a plan:", {
      reply_markup: { inline_keyboard: buttons }
    });
  } catch (e) {
    await alertAdmin(e, "upgrade command");
    ctx.reply("Error loading plans.");
  }
});

bot.action(/^buy_(7d|30d|90d)$/, async (ctx) => {
  try {
    const plan = ctx.match[1];
    const price = PREMIUM_PRICES;
    await ctx.replyWithInvoice({
      title: `Premium ${price.label}`,
      description: `Get premium access for ${price.days} days`,
      payload: `premium_${plan}_${ctx.from.id}`,
      provider_token: "",
      currency: "XTR",
      prices: [{ label: price.label, amount: price.stars }]
    });
  } catch (e) {
    await alertAdmin(e, "buy action");
    ctx.reply("Error creating invoice.");
  }
});

bot.on('pre_checkout_query', async (ctx) => {
  await ctx.answerPreCheckoutQuery(true);
});

bot.on('successful_payment', async (ctx) => {
  try {
    const payload = ctx.message.successful_payment.invoice_payload;
    const plan = payload.split('_')[1];
    const userId = parseInt(payload.split('_')[2]);
    const days = PREMIUM_PRICES.days;
    const newExp = Math.floor(Date.now() / 1000) + days * 86400;
    await updateUser(userId, 'exp', newExp);
    await bot.telegram.sendMessage(userId, `✅ Premium activated for ${days} days!`);
  } catch (e) {
    await alertAdmin(e, "successful_payment");
  }
});

bot.command('giveprem', async (ctx) => {
  try {
    if (ctx.from.id !== ADMIN_ID) return ctx.reply("Not allowed");
    const parts = ctx.message.text.split(' ');
    const targetId = parseInt(parts[1]);
    let days = parseInt(parts[2]) || 30;
    if (!targetId) return ctx.reply("Use: /giveprem USERID DAYS\nUse 9999 for lifetime");
    
    if (days === 9999) days = 99999;
    const newExp = Math.floor(Date.now() / 1000) + days * 86400;
    await updateUser(targetId, 'exp', newExp);
    
    const msg = days > 50000 ? "Lifetime premium activated" : `${days}d premium activated`;
    ctx.reply(`✅ ${msg} for ${targetId}`);
    await bot.telegram.sendMessage(targetId, `🎉 You now have premium!`).catch(()=>{});
  } catch (e) {
    await alertAdmin(e, "giveprem command");
    ctx.reply("Error giving premium.");
  }
});

bot.command('setprem', async (ctx) => {
  if (ctx.from.id !== ADMIN_ID) return ctx.reply("Not allowed");
  const parts = ctx.message.text.split(' ');
  const targetId = parseInt(parts[1]);
  if (!targetId) return ctx.reply("Use: /setprem USERID");
  
  const newExp = Math.floor(Date.now() / 1000) + 100000 * 86400;
  await updateUser(targetId, 'exp', newExp);
  ctx.reply(`✅ Lifetime premium activated for ${targetId}`);
  await bot.telegram.sendMessage(targetId, "🎉 You now have lifetime premium!").catch(()=>{});
});

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
