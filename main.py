import os
import json
import asyncio
import base64
import time
import logging
import html as html_lib
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
MODEL_NAME = os.getenv("MODEL_NAME", "local-model")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))

# Carrega e converte a lista de usuários permitidos (set para O(1) lookup)
ALLOWED_USER_IDS_ENV = os.getenv("ALLOWED_USER_IDS", "")
if ALLOWED_USER_IDS_ENV.strip():
    ALLOWED_USER_IDS = {int(x.strip()) for x in ALLOWED_USER_IDS_ENV.split(",") if x.strip()}
else:
    ALLOWED_USER_IDS = set()

client = AsyncOpenAI(
    base_url=LM_STUDIO_URL,
    api_key="lm-studio"
)

# Estrutura: { user_id: { "current": "1", "sessions": { "1": [{"role": "system", "content": "..."}, ...] } } }
user_histories = {}
MAX_HISTORY_LENGTH = 800
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "histories.json")

# Lock para evitar race conditions na persistência
history_lock = asyncio.Lock()

async def load_history():
    """Carrega o histórico salvo no disco e converte se for da versão antiga."""
    global user_histories
    if os.path.exists(HISTORY_FILE):
        try:
            data = await asyncio.to_thread(_read_history_file)
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

def _read_history_file():
    """Leitura síncrona do arquivo de histórico (executada em thread)."""
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_history_file(data_str: str):
    """Escrita síncrona do arquivo de histórico (executada em thread)."""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        f.write(data_str)

async def save_history():
    """Salva o histórico atual no disco de forma segura (com lock e I/O em thread)."""
    async with history_lock:
        try:
            data_str = json.dumps(user_histories, ensure_ascii=False, indent=2)
            await asyncio.to_thread(_write_history_file, data_str)
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

def _sanitize_message_for_history(user_message, caption: str = "") -> str:
    """Remove base64 de imagens antes de persistir no histórico."""
    if isinstance(user_message, list):
        for item in user_message:
            if item.get("type") == "text":
                return f"[Imagem analisada com legenda: '{item.get('text', '')}']"
        return "[Imagem recebida]"
    return user_message

async def get_llm_stream(user_id: int, user_message):
    init_user_session(user_id)
    curr = user_histories[user_id]["current"]
    session_history = user_histories[user_id]["sessions"][curr]
    
    # Adiciona a mensagem do usuário ao histórico
    session_history.append({"role": "user", "content": user_message})
    
    # Limpa base64 imediatamente no histórico persistido, mantendo o original para a API
    sanitized = _sanitize_message_for_history(user_message)
    if sanitized != user_message:
        # Salvar versão limpa no disco, mas manter original em memória para a chamada API
        original_content = session_history[-1]["content"]
        session_history[-1]["content"] = sanitized
        await save_history()
        session_history[-1]["content"] = original_content  # restaurar para a chamada API
    else:
        await save_history()
    
    bot_response = ""
    try:
        response_stream = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=session_history,
            temperature=TEMPERATURE,
            stream=True
        )
        
        async for chunk in response_stream:
            delta = chunk.choices[0].delta.content
            if delta is not None:
                bot_response += delta
                yield bot_response
                
    except Exception as e:
        logger.error(f"Erro ao contatar LM Studio: {e}")
        yield f"Desculpe, ocorreu um erro ao processar sua mensagem: {e}"
    finally:
        # Salvar a mensagem do usuário limpa (sem base64) e a resposta (mesmo parcial)
        if isinstance(user_message, list):
            session_history[-1]["content"] = sanitized
        
        if bot_response:
            session_history.append({"role": "assistant", "content": bot_response})
        else:
            # Se não houve resposta alguma, remover a mensagem do usuário para manter consistência
            session_history.pop()
        
        # Truncar histórico com while para garantir o limite
        while len(session_history) > MAX_HISTORY_LENGTH + 1:
            session_history.pop(1)
            session_history.pop(1)
            
        await save_history()

def _format_to_html(text: str) -> str:
    """Converte Markdown básico para HTML do Telegram de forma segura."""
    # Primeiro escapa caracteres HTML especiais
    text = html_lib.escape(text)
    
    # Blocos de código (``` ... ```) — processar antes do inline
    import re
    
    # Code blocks: ```lang\ncode\n``` → <pre><code>code</code></pre>
    def replace_code_block(match):
        code = match.group(2)
        return f"<pre><code>{code}</code></pre>"
    text = re.sub(r'```(\w*)\n(.*?)```', replace_code_block, text, flags=re.DOTALL)
    
    # Inline code: `code` → <code>code</code>
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    
    # Bold + Italic: ***text*** → <b><i>text</i></b>
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', text)
    
    # Bold: **text** → <b>text</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    
    # Italic: *text* → <i>text</i> (mas não dentro de tags já processadas)
    text = re.sub(r'(?<!\w)\*([^\*]+?)\*(?!\w)', r'<i>\1</i>', text)
    
    # Strikethrough: ~~text~~ → <s>text</s>
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)
    
    return text

# ==== COMANDOS DO TELEGRAM ====

HELP_TEXT = (
    "🤖 <b>Bot Telegram + LM Studio</b>\n\n"
    "Comandos disponíveis:\n"
    "/start - Inicia a conversa com o bot\n"
    "/help - Mostra esta mensagem de ajuda\n"
    "/new - Cria um novo chat limpo\n"
    "/chats - Lista todos os chats salvos\n"
    "/switch &lt;id&gt; - Alterna para um chat específico\n"
    "/clear - Limpa a memória do chat atual\n"
    "/delete &lt;id&gt; - Exclui um chat permanentemente"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id):
        logger.warning(f"Acesso negado para o ID {user.id} ({user.first_name}) no /start")
        await update.message.reply_text(f"Acesso negado. Seu ID do Telegram é: {user.id}")
        return
        
    init_user_session(user.id)
    await update.message.reply_text(
        f"Olá, {html_lib.escape(user.first_name)}! Eu sou um bot rodando via LM Studio.\n\n"
        f"{HELP_TEXT}",
        parse_mode=ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id): return
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)

async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id): return
    init_user_session(user_id)
    
    existing_ids = [int(k) for k in user_histories[user_id]["sessions"].keys() if k.isdigit()]
    next_id = str(max(existing_ids) + 1) if existing_ids else "1"
    
    user_histories[user_id]["current"] = next_id
    user_histories[user_id]["sessions"][next_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await save_history()
    
    await update.message.reply_text(f"✨ Novo chat iniciado! Você está agora na Sessão {next_id}.")

async def list_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id): return
    init_user_session(user_id)
    
    sessions = user_histories[user_id]["sessions"]
    curr = user_histories[user_id]["current"]
    
    msg_lines = ["📂 <b>Seus chats salvos:</b>\n"]
    for sid, msgs in sessions.items():
        preview = "Vazio"
        for m in msgs:
            if m["role"] == "user":
                content = m["content"] if isinstance(m["content"], str) else "[Imagem]"
                preview = content[:35] + "..." if len(content) > 35 else content
                break
                
        prefix = "👉" if sid == curr else "💬"
        msg_lines.append(f"{prefix} <b>Sessão {sid}</b>: {html_lib.escape(preview)}")
        
    msg_lines.append("\nUse /switch ID para trocar de chat.")
    await update.message.reply_text("\n".join(msg_lines), parse_mode=ParseMode.HTML)

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
        await save_history()
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
        await save_history()
        await update.message.reply_text(f"🗑️ Sessão {target_id} apagada permanentemente.")
    else:
        await update.message.reply_text("❌ Chat não encontrado.")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_allowed(user_id): return
    init_user_session(user_id)
    
    curr = user_histories[user_id]["current"]
    user_histories[user_id]["sessions"][curr] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await save_history()
    await update.message.reply_text(f"🧹 Memória da Sessão {curr} limpa. Pode começar um novo assunto!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_user_allowed(user.id):
        logger.warning(f"Tentativa de acesso negado: {user.id}")
        await update.message.reply_text(f"Acesso negado. Seu ID é {user.id}.")
        return

    is_photo = bool(update.message.photo)
    
    if is_photo:
        photo_file = await update.message.photo[-1].get_file()
        byte_array = await photo_file.download_as_bytearray()
        base64_image = base64.b64encode(byte_array).decode('utf-8')
        
        caption = update.message.caption or "Descreva o que há nesta imagem detalhadamente."
        message_content = [
            {"type": "text", "text": caption},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
        ]
    else:
        message_content = update.message.text
        if not message_content: 
            return # Ignora figurinhas, áudios e vídeos (por enquanto)

    loading_message = await update.message.reply_text("⏳ Pensando...")
    
    last_edit_time = time.time()
    final_text = ""
    
    try:
        async for partial_text in get_llm_stream(user.id, message_content):
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
        
        MAX_LENGTH = 4000
        formatted_text = _format_to_html(final_text)
        
        if len(formatted_text) <= MAX_LENGTH:
            try:
                await loading_message.edit_text(formatted_text, parse_mode=ParseMode.HTML)
            except Exception:
                try:
                    # Fallback: enviar sem formatação se o HTML estiver malformado
                    await loading_message.edit_text(final_text[:MAX_LENGTH])
                except Exception:
                    await loading_message.edit_text("Erro interno ao formatar a sua mensagem.")
        else:
            # Dividir em partes respeitando o limite
            try:
                await loading_message.edit_text(formatted_text[:MAX_LENGTH], parse_mode=ParseMode.HTML)
            except Exception:
                await loading_message.edit_text(final_text[:MAX_LENGTH])
            
            for i in range(MAX_LENGTH, len(formatted_text), MAX_LENGTH):
                chunk = formatted_text[i:i+MAX_LENGTH]
                try:
                    await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
                except Exception:
                    await update.message.reply_text(final_text[i:i+MAX_LENGTH])

    except Exception as e:
        logger.error(f"Erro durante o handle_message: {e}")
        await update.message.reply_text("Desculpe, ocorreu um erro inesperado.")

async def check_lm_studio():
    """Health check para verificar se o LM Studio está acessível."""
    try:
        models = await client.models.list()
        model_names = [m.id for m in models.data]
        logger.info(f"✅ LM Studio conectado. Modelos disponíveis: {model_names}")
        return True
    except Exception as e:
        logger.warning(f"⚠️ LM Studio não acessível em {LM_STUDIO_URL}: {e}")
        logger.warning("O bot será iniciado mesmo assim. Certifique-se de iniciar o LM Studio antes de enviar mensagens.")
        return False

async def post_init(application):
    """Executado após a inicialização do bot."""
    await load_history()
    await check_lm_studio()

if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "seu_token_do_telegram_aqui":
        logger.error("Token do Telegram não encontrado! Configure o arquivo .env.")
        exit(1)
    
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('new', new_chat))
    application.add_handler(CommandHandler('chats', list_chats))
    application.add_handler(CommandHandler('switch', switch_chat))
    application.add_handler(CommandHandler('delete', delete_chat))
    application.add_handler(CommandHandler('clear', clear))
    
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & (~filters.COMMAND), handle_message))
    
    logger.info("Bot iniciado! Pressione Ctrl+C para parar.")
    application.run_polling()
