# DEPIN Farmer Bot

Telegram bot for DEPIN farming, airdrops, and AI dev help with Telegram Stars monetization.

## Commands
/ask <q> - Ask AI
/dev <topic> - Dev guides & solutions
/nodes - Top DEPIN projects. Free: 3, Premium: 10
/airdrop - Active airdrops. Free: 2, Premium: full list + est value
/farm <topic> - Farming tips
/upgrade - Get premium
/stats - Your usage
/status - Check premium
/id - Show your ID
/feedback <msg> - Send feedback to admin

## Deploy
1. Push to GitHub
2. Import to Vercel
3. Add env vars: TELEGRAM_BOT_TOKEN, GROQ_API_KEY, REDIS_URL
4. Deploy
5. Set webhook: https://api.telegram.org/botTOKEN/setWebhook?url=YOUR_URL/api/webhook

ADMIN_ID 8429170788 has lifetime premium.
Use /setprem USERID for lifetime premium.
