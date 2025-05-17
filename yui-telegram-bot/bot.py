import os
import openai
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# Variáveis de ambiente
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = "https://yui-telegram-bot-production.up.railway.app"
AUTHORIZED_USER_ID = 1676104684  # Substitua por seu próprio ID, se necessário

# Configura OpenAI
openai.api_key = OPENAI_API_KEY

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    await update.message.reply_text("✅ Bot ativo via Webhook!")

# Mensagens de texto comuns
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return

    user_message = update.message.text
    await update.message.chat.send_action(action="typing")

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": user_message}]
        )
        reply = response["choices"][0]["message"]["content"]
    except Exception as e:
        reply = f"❌ Erro ao consultar IA: {e}"

    await update.message.reply_text(reply)

# Instância do bot
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Webhook handler
async def handle_webhook(request):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

# Servidor aiohttp
web_app = web.Application()
web_app.router.add_post(f"/{TELEGRAM_TOKEN}", handle_webhook)

# Função principal
async def run():
    await app.initialize()
    await app.bot.set_webhook(f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")
    await app.start()
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    print(f"🌐 Webhook ativo em {WEBHOOK_URL}")
    await site.start()

# Execução
if __name__ == "__main__":
    asyncio.run(run())
