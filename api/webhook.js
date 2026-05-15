const { Telegraf } = require('telegraf');
const Redis = require('ioredis');
const Groq = require('groq-sdk');

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const GROQ_API_KEY = process.env.GROQ_API_KEY;
const ADMIN_ID = 8429170788;
const REDIS_URL = process.env.REDIS_URL;

if (!BOT_TOKEN) throw new Error("TELEGRAM_BOT_TOKEN not set");

// ... rest of the code from before ...
// Use the full webhook.js code I sent in the last message here
module.exports = async (req, res) => {
  if (req.method !== 'POST') return res.status(200).send('OK');
  await bot.handleUpdate(req.body);
  res.status(200).send('OK');
};
