import os
import logging
import signal
import asyncio
import sqlite3
import datetime
import random
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

# Configuração de logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Variáveis de ambiente
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
AUTHORIZED_USER_IDS = [int(id) for id in os.getenv("AUTHORIZED_USER_IDS", "1676104684").split(",")]

# Configuração do SQLite
DB_PATH = "/app/data/history.db"

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                hour INTEGER,
                minute INTEGER,
                frequency TEXT,
                message TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER PRIMARY KEY,
                spontaneous_enabled INTEGER
            )
        """)
        conn.commit()

# Funções do banco de dados
def load_history(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role, content FROM history WHERE chat_id = ? ORDER BY timestamp", (chat_id,))
        return [{"role": role, "content": content} for role, content in cursor.fetchall()]

def save_history(chat_id, role, content):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, role, content))
        conn.commit()

def clear_history(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM history WHERE chat_id = ?", (chat_id,))
        conn.commit()

def get_personality(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT instruction FROM personality WHERE chat_id = ?", (chat_id,))
        result = cursor.fetchone()
        return result[0] if result else "Você é Yui, uma assistente emocional alegre e empática."

def save_personality(chat_id, instruction):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO personality (chat_id, instruction) VALUES (?, ?)",
            (chat_id, instruction)
        )
        conn.commit()

def save_schedule(chat_id, hour, minute, frequency, message):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO schedules (chat_id, hour, minute, frequency, message) VALUES (?, ?, ?, ?, ?)",
            (chat_id, hour, minute, frequency, message)
        )
        conn.commit()

def load_schedules():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, hour, minute, frequency, message FROM schedules")
        return [{"chat_id": row[0], "hour": row[1], "minute": row[2], "frequency": row[3], "message": row[4]} for row in cursor.fetchall()]

def get_spontaneous_enabled(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT spontaneous_enabled FROM settings WHERE chat_id = ?", (chat_id,))
        result = cursor.fetchone()
        return bool(result[0]) if result else False

def set_spontaneous_enabled(chat_id, enabled):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (chat_id, spontaneous_enabled) VALUES (?, ?)",
            (chat_id, 1 if enabled else 0)
        )
        conn.commit()

# Validação de variáveis
if not TELEGRAM_TOKEN or not WEBHOOK_URL:
    logger.error("TELEGRAM_TOKEN e WEBHOOK_URL devem ser definidos")
    raise ValueError("Variáveis de ambiente obrigatórias não definidas")
if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY deve ser definida")
    raise ValueError("GEMINI_API_KEY não configurada")

# Configura Gemini
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-1.5-flash")

# Inicializa o banco de dados
init_db()

# Estados do ConversationHandler
ASK_AUTOSCHEDULE = 0

# Função para chamar a API do Gemini
async def call_gemini(messages, personality):
    try:
        prompt = f"{personality}\n" + "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])
        response = gemini_model.generate_content(prompt)
        if not response.text:
            raise Exception("Resposta vazia da Gemini API")
        return response.text
    except Exception as e:
        logger.error(f"Erro na API do Gemini: {e}")
        return f"❌ Erro ao processar com Gemini: {e}. Tente novamente mais tarde."

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
        "/schedule - Agenda mensagens (ex.: /schedule 08:00 daily Frase motivacional)\n"
        "/autoschedule - Sugere horários automáticos para mensagens\n"
        "/spontaneous - Ativa/desativa mensagens espontâneas (ex.: /spontaneous on)\n"
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

# Comando /schedule
async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    chat_id = update.effective_chat.id
    if len(context.args) < 3:
        await update.message.reply_text(
            "Uso: /schedule <hora> <frequência> <mensagem>\n"
            "Exemplo: /schedule 08:00 daily Frase motivacional\n"
            "Frequência: daily, weekly, once"
        )
        return
    try:
        time_str = context.args[0]
        frequency = context.args[1].lower()
        message = " ".join(context.args[2:])
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Hora inválida")
        if frequency not in ["daily", "weekly", "once"]:
            raise ValueError("Frequência inválida")
        save_schedule(chat_id, hour, minute, frequency, message)
        await update.message.reply_text(f"✅ Mensagem agendada para {time_str} ({frequency}): {message}")
    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao agendar: {e}")

# Comando /spontaneous
async def spontaneous(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    chat_id = update.effective_chat.id
    if not context.args or context.args[0].lower() not in ["on", "off"]:
        enabled = get_spontaneous_enabled(chat_id)
        await update.message.reply_text(
            f"Mensagens espontâneas: {'Ativadas' if enabled else 'Desativadas'}\n"
            "Use: /spontaneous on|off"
        )
        return
    enabled = context.args[0].lower() == "on"
    set_spontaneous_enabled(chat_id, enabled)
    await update.message.reply_text(f"✅ Mensagens espontâneas {'ativadas' if enabled else 'desativadas'}.")

# Comando /autoschedule
async def autoschedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text(
            "Uso: /autoschedule <tipo>\n"
            "Exemplo: /autoschedule motivacional\n"
            "Tipos: motivacional, check-in"
        )
        return
    context.user_data["autoschedule_type"] = " ".join(context.args)
    await update.message.reply_text(
        f"Que tal agendar mensagens de {context.user_data['autoschedule_type']} às 08:00 e 20:00 diariamente? "
        "Responda 'Sim' para confirmar ou sugira outros horários (ex.: 09:00, 21:00)."
    )
    return ASK_AUTOSCHEDULE

# Resposta para autoschedule
async def handle_autoschedule_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("🚫 Acesso negado.")
        return ConversationHandler.END
    chat_id = update.effective_chat.id
    response = update.message.text.strip().lower()
    message_type = context.user_data.get("autoschedule_type", "motivacional")
    try:
        if response == "sim":
            save_schedule(chat_id, 8, 0, "daily", f"Mensagem {message_type}")
            save_schedule(chat_id, 20, 0, "daily", f"Mensagem {message_type}")
            await update.message.reply_text(f"✅ Agendado: {message_type} às 08:00 e 20:00 diariamente!")
        else:
            times = response.split(",")
            if len(times) != 2:
                raise ValueError("Por favor, sugira dois horários (ex.: 09:00, 21:00)")
            for time in times:
                hour, minute = map(int, time.strip().split(":"))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError("Hora inválida")
                save_schedule(chat_id, hour, minute, "daily", f"Mensagem {message_type}")
            await update.message.reply_text(f"✅ Agendado: {message_type} às {times[0]} e {times[1]} diariamente!")
    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao agendar: {e}")
    return ConversationHandler.END

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
    personality = get_personality(chat_id)
    full_history = context.user_data["history"] + [{"role": "user", "content": user_message}]
    save_history(chat_id, "user", user_message)

    reply = await call_gemini(full_history, personality)
    context.user_data["history"].append({"role": "assistant", "content": reply})
    save_history(chat_id, "assistant", reply)
    await update.message.reply_text(reply)

# Tarefa para enviar mensagens agendadas e espontâneas
async def schedule_task(context: ContextTypes.DEFAULT_TYPE):
    while True:
        now = datetime.datetime.now()
        schedules = load_schedules()
        for schedule in schedules:
            chat_id = schedule["chat_id"]
            hour = schedule["hour"]
            minute = schedule["minute"]
            frequency = schedule["frequency"]
            message = schedule["message"]
            if now.hour == hour and now.minute == minute:
                try:
                    personality = get_personality(chat_id)
                    reply = await call_gemini([{"role": "user", "content": f"Crie uma {message}"}], personality)
                    await context.bot.send_message(chat_id=chat_id, text=reply)
                except Exception as e:
                    logger.error(f"Erro ao enviar mensagem agendada para {chat_id}: {e}")

        # Mensagens espontâneas
        for chat_id in AUTHORIZED_USER_IDS:
            if get_spontaneous_enabled(chat_id) and random.random() < 0.01:  # ~1% de chance por minuto
                try:
                    personality = get_personality(chat_id)
                    history = load_history(chat_id)[-5:]  # Últimas 5 mensagens
                    reply = await call_gemini(
                        history + [{"role": "user", "content": "Envie um lembrete motivacional ou check-in emocional."}],
                        personality
                    )
                    await context.bot.send_message(chat_id=chat_id, text=reply)
                except Exception as e:
                    logger.error(f"Erro ao enviar mensagem espontânea para {chat_id}: {e}")

        await asyncio.sleep(60)  # Verifica a cada minuto

# Instância do bot
app = Application.builder().token(TELEGRAM_TOKEN).build()

# Configura ConversationHandler
conv_handler = ConversationHandler(
    entry_points=[
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
        CommandHandler("autoschedule", autoschedule)
    ],
    states={
        ASK_AUTOSCHEDULE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_autoschedule_response)],
    },
    fallbacks=[],
)
app.add_handler(conv_handler)
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("clear", clear))
app.add_handler(CommandHandler("personality", set_personality))
app.add_handler(CommandHandler("schedule", schedule))
app.add_handler(CommandHandler("spontaneous", spontaneous))

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
    app.job_queue.run_once(schedule_task, 0)  # Inicia a tarefa de agendamento
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
