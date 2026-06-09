import os
import json
import time
import logging
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Configura logs para facilitar o debug
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "")

# Carrega e converte a lista de usuários permitidos
ALLOWED_USER_IDS_ENV = os.getenv("ALLOWED_USER_IDS", "")
if ALLOWED_USER_IDS_ENV.strip():
    ALLOWED_USER_IDS = [int(x.strip()) for x in ALLOWED_USER_IDS_ENV.split(",") if x.strip()]
else:
    ALLOWED_USER_IDS = []

client = AsyncOpenAI(
    base_url=LM_STUDIO_URL,
    api_key="lm-studio"
)

# Estrutura: { user_id: { "current": "1", "sessions": { "1": [{"role": "system", "content": "..."}, ...] } } }
user_histories = {}
MAX_HISTORY_LENGTH = 1000 
HISTORY_FILE = "histories.json"

def load_history():
    """Carrega o histórico salvo no disco e converte se for da versão antiga."""
    global user_histories
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    user_id = int(k)
                    if isinstance(v, list):
                        # Formato antigo detectado. Migrando...
                        user_histories[user_id] = {
                            "current": "1",
                            "sessions": {"1": v}
                        }
                    else:
                        user_histories[user_id] = v
        except Exception as e:
            logger.error(f"Erro ao carregar histórico: {e}")

def save_history():
    """Salva o histórico atual no disco."""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(user_histories, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erro ao salvar histórico: {e}")

def is_user_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS

def init_user_session(user_id: int):
    """Garante que a estrutura de dados do usuário exista."""
    if user_id not in user_histories:
        user_histories[user_id] = {
            "current": "1",
            "sessions": {
                "1": [{"role": "system", "content": SYSTEM_PROMPT}]
            }
        }
    
    # Se a sessão atual foi apagada e não existe mais, recria a padrão
    curr = user_histories[user_id]["current"]
    if curr not in user_histories[user_id]["sessions"]:
        user_histories[user_id]["sessions"][curr] = [{"role": "system", "content": SYSTEM_PROMPT}]

async def get_llm_stream(user_id: int, user_message: str):
    init_user_session(user_id)
    curr = user_histories[user_id]["current"]
    session_history = user_histories[user_id]["sessions"][curr]
        
    session_history.append({"role": "user", "content": user_message})
    save_history()
    
    try:
        response_stream = await client.chat.completions.create(
            model="local-model",
            messages=session_history,
            temperature=0.7,
            stream=True
        )
        
        bot_response = ""
        async for chunk in response_stream:
            delta = chunk.choices[0].delta.content
            if delta is not None:
                bot_response += delta
                yield bot_response
                
        # Fim da geração
        session_history.append({"role": "assistant", "content": bot_response})
        
        if len(session_history) > MAX_HISTORY_LENGTH + 1:
            session_history.pop(1)
            session_history.pop(1)
            
        save_history()
            
    except Exception as e:
        logger.error(f"Erro ao contatar LM Studio: {e}")
        yield f"Desculpe, ocorreu um erro ao processar sua mensagem: {e}"

# ==== COMANDOS DO TELEGRAM ====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id):
        logger.warning(f"Acesso negado para o ID {user.id} ({user.first_name}) no /start")
        await update.message.reply_text(f"Acesso negado. Seu ID do Telegram é: {user.id}")
        return
        
    init_user_session(user.id)
    await update.message.reply_text(
        f"Olá, {user.first_name}! Eu sou um bot rodando via LM Studio.\n"
        "Comandos disponíveis:\n"
        "/new - Iniciar uma nova conversa em branco\n"
        "/chats - Ver a lista das suas conversas salvas\n"
        "/switch <id> - Trocar para uma conversa antiga\n"
        "/clear - Apagar a conversa atual\n"
        "/delete <id> - Apagar uma conversa específica da memória"
    )

async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id): return
    init_user_session(user_id)
    
    existing_ids = [int(k) for k in user_histories[user_id]["sessions"].keys() if k.isdigit()]
    next_id = str(max(existing_ids) + 1) if existing_ids else "1"
    
    user_histories[user_id]["current"] = next_id
    user_histories[user_id]["sessions"][next_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    save_history()
    
    await update.message.reply_text(f"✨ Novo chat iniciado! Você está agora na Sessão {next_id}.")

async def list_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id): return
    init_user_session(user_id)
    
    sessions = user_histories[user_id]["sessions"]
    curr = user_histories[user_id]["current"]
    
    msg_lines = ["📂 **Seus chats salvos:**\n"]
    for sid, msgs in sessions.items():
        preview = "Vazio"
        for m in msgs:
            if m["role"] == "user":
                preview = m["content"][:35] + "..." if len(m["content"]) > 35 else m["content"]
                break
                
        prefix = "👉" if sid == curr else "💬"
        msg_lines.append(f"{prefix} **Sessão {sid}**: {preview}")
        
    msg_lines.append("\nUse `/switch ID` para trocar de chat.")
    await update.message.reply_text("\n".join(msg_lines), parse_mode=ParseMode.MARKDOWN)

async def switch_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id): return
    init_user_session(user_id)
    
    if not context.args:
        await update.message.reply_text("Formato incorreto. Use: /switch 1")
        return
        
    target_id = context.args[0]
    if target_id in user_histories[user_id]["sessions"]:
        user_histories[user_id]["current"] = target_id
        save_history()
        await update.message.reply_text(f"🔄 Trocado com sucesso para a Sessão {target_id}!")
    else:
        await update.message.reply_text("❌ Chat não encontrado. Use /chats para ver os IDs disponíveis.")

async def delete_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id): return
    init_user_session(user_id)
    
    if not context.args:
        await update.message.reply_text("Formato incorreto. Use: /delete 1")
        return
        
    target_id = context.args[0]
    if target_id in user_histories[user_id]["sessions"]:
        del user_histories[user_id]["sessions"][target_id]
        save_history()
        await update.message.reply_text(f"🗑️ Sessão {target_id} apagada permanentemente.")
    else:
        await update.message.reply_text("❌ Chat não encontrado.")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id): return
    init_user_session(user_id)
    
    curr = user_histories[user_id]["current"]
    user_histories[user_id]["sessions"][curr] = [{"role": "system", "content": SYSTEM_PROMPT}]
    save_history()
    await update.message.reply_text(f"🧹 Memória da Sessão {curr} limpa. Pode começar um novo assunto!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id):
        logger.warning(f"Tentativa de acesso negado: {user.id}")
        await update.message.reply_text(f"Acesso negado. Seu ID é {user.id}.")
        return

    text = update.message.text
    loading_message = await update.message.reply_text("⏳ Pensando...")
    
    last_edit_time = time.time()
    final_text = ""
    
    try:
        async for partial_text in get_llm_stream(user.id, text):
            final_text = partial_text
            current_time = time.time()
            
            if current_time - last_edit_time > 1.5:
                text_to_show = partial_text[:4000] + " ✍️" if len(partial_text) > 4000 else partial_text + " ✍️"
                try:
                    await loading_message.edit_text(text_to_show)
                except BadRequest:
                    pass
                last_edit_time = current_time

        if not final_text:
            await loading_message.edit_text("A IA não retornou nenhuma resposta.")
            return
            
        final_text = str(final_text).replace('**', '*')
        MAX_LENGTH = 4000
        
        if len(final_text) <= MAX_LENGTH:
            try:
                await loading_message.edit_text(final_text, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                try:
                    await loading_message.edit_text(final_text)
                except Exception:
                    await loading_message.edit_text("Erro interno ao formatar a sua mensagem.")
        else:
            await loading_message.edit_text(final_text[:MAX_LENGTH])
            for i in range(MAX_LENGTH, len(final_text), MAX_LENGTH):
                await update.message.reply_text(final_text[i:i+MAX_LENGTH])

    except Exception as e:
        logger.error(f"Erro durante o handle_message: {e}")
        await update.message.reply_text("Desculpe, ocorreu um erro inesperado.")

if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "seu_token_do_telegram_aqui":
        logger.error("Token do Telegram não encontrado! Configure o arquivo .env.")
        exit(1)
        
    load_history()
    
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('new', new_chat))
    application.add_handler(CommandHandler('chats', list_chats))
    application.add_handler(CommandHandler('switch', switch_chat))
    application.add_handler(CommandHandler('delete', delete_chat))
    application.add_handler(CommandHandler('clear', clear))
    
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    logger.info("Bot iniciado! Pressione Ctrl+C para parar.")
    application.run_polling()
