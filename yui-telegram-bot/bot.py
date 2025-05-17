import os
import asyncio
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
)
from aiohttp import web

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = "https://yui-telegram-bot-production.up.railway.app"
AUTHORIZED_USER_ID = 1676104684

# Comando inicial, restrito
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    await update.message.reply_text("✅ Bot ativo via Webhook!")

# Inicializa app do Telegram
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))

# Handler HTTP para processar updates
async def handle(request):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

# Cria o servidor aiohttp
web_app = web.Application()
web_app.router.add_post(f"/{BOT_TOKEN}", handle)

# Inicia o bot e o webhook
async def run():
    await app.initialize()
    await app.bot.set_webhook(f"{WEBHOOK_URL}/{BOT_TOKEN}")
    await app.start()
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    print(f"🌐 Webhook ativo em {WEBHOOK_URL}")
    await site.start()

asyncio.run(run())
