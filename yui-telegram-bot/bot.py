import os
import logging
import signal
import asyncio
import sqlite3
from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
import google.generativeai as genai
from openai import OpenAI, APIError, RateLimitError, AuthenticationError

# Configuração de logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Variáveis de ambiente
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
AUTHORIZED_USER_IDS = [int(id) for id in os.getenv("AUTHORIZED_USER_IDS", "1676104684").split(",")]

# Configuração do SQLite
DB_PATH = "history.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history (
                chat_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS personality (
                chat_id INTEGER PRIMARY KEY,
                instruction TEXT
            )
        """)
        conn.commit()

# Função para carregar histórico
def load_history(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role, content FROM history WHERE chat_id = ? ORDER BY timestamp", (chat_id,))
        return [{"role": role, "content": content} for role, content in cursor.fetchall()]

# Função para salvar mensagem no histórico
def save_history(chat_id, role, content):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, role, content))
        conn.commit()

# Função para limpar histórico
def clear_history(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM history WHERE chat_id = ?", (chat_id,))
        conn.commit()

# Função para obter instrução de personalidade
def get_personality(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT instruction FROM personality WHERE chat_id = ?", (chat_id,))
        result = cursor.fetchone()
        return result[0] if result else "Você é Yui, uma assistente emocional alegre e empática."

# Função para salvar instrução de personalidade
def save_personality(chat_id, instruction):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO personality (chat_id, instruction) VALUES (?, ?)",
            (chat_id, instruction)
        )
        conn.commit()

# Validação de variáveis
if not TELEGRAM_TOKEN or not WEBHOOK_URL:
    logger.error("TELEGRAM_TOKEN e WEBHOOK_URL devem ser definidos")
    raise ValueError("Variáveis de ambiente obrigatórias não definidas")
if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY deve ser definida como API primária")
    raise ValueError("OPENAI_API_KEY não configurada")

# Configura OpenAI
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Configura Gemini (se disponível)
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel("gemini-1.5-flash")
else:
    gemini_model = None

# Inicializa o banco de dados
init_db()

# Estados do ConversationHandler
ASK_GEMINI = 1

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    chat_id = update.effective_chat.id
    context.user_data["history"] = load_history(chat_id)
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
        "/personality - Define a personalidade da Yui (ex.: /personality séria e profissional)\n"
        "💬 Envie qualquer mensagem para conversar comigo!"
    )
    await update.message.reply_text(help_text)

# Comando /clear
async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    chat_id = update.effective_chat.id
    context.user_data["history"] = []
    clear_history(chat_id)
    await update.message.reply_text("🧹 Histórico de conversa limpo!")

# Comando /personality
async def set_personality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    chat_id = update.effective_chat.id
    if not context.args:
        current = get_personality(chat_id)
        await update.message.reply_text(f"Personalidade atual: {current}\nUse /personality <descrição> para mudar.")
        return
    instruction = " ".join(context.args)
    save_personality(chat_id, instruction)
    await update.message.reply_text(f"✅ Personalidade definida como: {instruction}")

# Mensagens de texto comuns
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        return
    user_message = update.message.text.strip()
    if len(user_message) < 3:
        await update.message.reply_text("Por favor, envie uma mensagem mais detalhada! 😊")
        return
    chat_id = update.effective_chat.id
    await update.message.chat.send_action(action="typing")
    context.user_data.setdefault("history", load_history(chat_id))
    # Adiciona instrução de sistema
    personality = get_personality(chat_id)
    full_history = [{"role": "system", "content": personality}] + context.user_data["history"]
    full_history.append({"role": "user", "content": user_message})
    save_history(chat_id, "user", user_message)

    reply = None
    # Tenta OpenAI primeiro
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=full_history,
            max_tokens=500
        )
        reply = response.choices[0].message.content
        context.user_data["history"].append({"role": "assistant", "content": reply})
        save_history(chat_id, "assistant", reply)
    except RateLimitError:
        logger.warning("Limite de requisições da OpenAI atingido")
        if gemini_model:
            context.user_data["pending_message"] = user_message
            await update.message.reply_text(
                "❌ Limite de requisições atingido na OpenAI. Deseja prosseguir com a Gemini API? Responda 'Sim' para continuar ou qualquer outra coisa para parar."
            )
            return ASK_GEMINI
        else:
            reply = "❌ Limite de requisições atingido na OpenAI e Gemini não configurada. Tente novamente mais tarde."
    except AuthenticationError:
        reply = "❌ Erro de autenticação com a OpenAI."
        logger.error("Chave da OpenAI inválida")
    except APIError as e:
        reply = "❌ Erro na API da OpenAI. Tente novamente."
        logger.error(f"Erro na API da OpenAI: {e}")
    except Exception as e:
        reply = "❌ Erro ao processar com OpenAI. Tente novamente."
        logger.error(f"Erro inesperado na OpenAI: {e}")

    if reply:
        await update.message.reply_text(reply)
    return ConversationHandler.END

# Resposta para o fallback do Gemini
async def handle_gemini_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return ConversationHandler.END
    chat_id = update.effective_chat.id
    response = update.message.text.strip().lower()
    if response != "sim":
        await update.message.reply_text("Ok, vamos pausar por agora. Tente novamente mais tarde! 😊")
        return ConversationHandler.END

    # Tenta Gemini
    personality = get_personality(chat_id)
    full_history = [{"role": "system", "content": personality}] + context.user_data["history"]
    try:
        prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in full_history])
        response = gemini_model.generate_content(prompt)
        if not response.text:
            raise Exception("Resposta vazia da Gemini API")
        reply = response.text
        context.user_data["history"].append({"role": "assistant", "content": reply})
        save_history(chat_id, "assistant", reply)
    except Exception as e:
        reply = "❌ Erro ao processar com Gemini. Tente novamente mais tarde."
        logger.error(f"Erro na API do Gemini: {e}")

    await update.message.reply_text(reply)
    return ConversationHandler.END

# Instância do bot
app = Application.builder().token(TELEGRAM_TOKEN).build()

# Configura ConversationHandler
conv_handler = ConversationHandler(
    entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
    states={
        ASK_GEMINI: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gemini_choice)],
    },
    fallbacks=[],
)
app.add_handler(conv_handler)
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("clear", clear))
app.add_handler(CommandHandler("personality", set_personality))

# Webhook handler
async def webhook_handler(request):
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
web_app.router.add_post(f"/{TELEGRAM_TOKEN}", webhook_handler)

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
