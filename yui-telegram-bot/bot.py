import os
import logging
import signal
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import google.generativeai as genai

# Configuração de logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Variáveis de ambiente
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
AUTHORIZED_USER_IDS = [int(id) for id in os.getenv("AUTHORIZED_USER_IDS", "1676104684").split(",")]

# Validação de variáveis
if not TELEGRAM_TOKEN or not GEMINI_API_KEY or not WEBHOOK_URL:
    logger.error("TELEGRAM_TOKEN, GEMINI_API_KEY e WEBHOOK_URL devem ser definidos")
    raise ValueError("Variáveis de ambiente obrigatórias não definidas")

# Configura Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    context.user_data["history"] = []
    await update.message.reply_text("✅ Oi! Sou a Yui, sua parceira emocional. Como posso te ajudar hoje? Use /help para mais comandos!")

# Comando /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    help_text = (
        "📋 Comandos disponíveis:\n"
        "/start - Inicia o bot\n"
        "/help - Mostra esta mensagem\n"
        "/clear - Limpa o histórico de conversa\n"
        "💬 Envie qualquer mensagem para conversar comigo!"
    )
    await update.message.reply_text(help_text)

# Comando /clear
async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    context.user_data["history"] = []
    await update.message.reply_text("🧹 Histórico de conversa limpo!")

# Mensagens de texto comuns
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        return
    user_message = update.message.text.strip()
    if len(user_message) < 3:
        await update.message.reply_text("Por favor, envie uma mensagem mais detalhada! 😊")
        return
    await update.message.chat.send_action(action="typing")
    context.user_data.setdefault("history", [])
    context.user_data["history"].append({"role": "user", "content": user_message})
    try:
        # Converte histórico para prompt
        prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in context.user_data["history"]])
        response = model.generate_content(prompt)
        if not response.text:
            raise Exception("Resposta vazia da Gemini API")
        reply = response.text
        context.user_data["history"].append({"role": "assistant", "content": reply})
    except Exception as e:
        reply = "❌ Erro ao processar com Gemini. Tente novamente."
        logger.error(f"Erro na API do Gemini: {e}")
    await update.message.reply_text(reply)

# Instância do bot
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("clear", clear))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Webhook handler
async def handle_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, app.bot)
        if update:
            await app.process_update(update)
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Erro no webhook: {e}")
        return web.Response(status=400, text="Erro ao processar webhook")

# Servidor aiohttp
web_app = web.Application()
web_app.router.add_post(f"/{TELEGRAM_TOKEN}", handle_webhook)

# Função de shutdown
async def shutdown(runner):
    logger.info("Encerrando o bot...")
    await app.shutdown()
    await app.cleanup()
    await runner.cleanup()

# Função principal
async def run():
    await app.initialize()
    await app.bot.set_webhook(f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")
    await app.start()
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    logger.info(f"🌐 Webhook ativo em {WEBHOOK_URL}")
    await site.start()

# Execução
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    runner = web.AppRunner(web_app)
    loop.run_until_complete(run())
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: loop.create_task(shutdown(runner)))
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(shutdown(runner))
