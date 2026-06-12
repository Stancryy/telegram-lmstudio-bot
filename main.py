import os
import json
import asyncio
import base64
import re
import time
import logging
import html as html_lib
from typing import AsyncGenerator, Union
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.error import BadRequest, TimedOut
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

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

try:
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
    if not 0.0 <= TEMPERATURE <= 2.0:
        raise ValueError("TEMPERATURE fora do intervalo válido (0.0 - 2.0)")
except (ValueError, TypeError):
    logger.warning("TEMPERATURE inválido no .env, usando valor padrão 0.7")
    TEMPERATURE = 0.7

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

# Estrutura: { user_id: { "current": "1", "sessions": { "1": { "name": "Chat 1", "messages": [...] } } } }
user_histories: dict = {}
MAX_HISTORY_LENGTH = int(os.getenv("MAX_HISTORY_LENGTH", "800"))
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "histories.json")

# Constantes do Telegram e de streaming
MAX_TELEGRAM_MSG_LENGTH = 4096
STREAM_EDIT_INTERVAL = 1.0  # segundos entre edições durante streaming
CHAT_PREVIEW_LENGTH = 35

# Lock para evitar race conditions em TODAS as operações de histórico
history_lock = asyncio.Lock()

# Debounce para save: evita múltiplas escritas por mensagem
_save_task: asyncio.Task | None = None
SAVE_DEBOUNCE_SECONDS = 2.0


# ==== PERSISTÊNCIA ====

async def load_history() -> None:
    """Carrega o histórico salvo no disco e converte se for da versão antiga."""
    global user_histories
    async with history_lock:
        if os.path.exists(HISTORY_FILE):
            try:
                data = await asyncio.to_thread(_read_history_file)
                for k, v in data.items():
                    user_id = int(k)
                    if isinstance(v, list):
                        # Formato v1 (lista simples) → v2 (sessões)
                        user_histories[user_id] = {
                            "current": "1",
                            "sessions": {"1": {"name": "Chat 1", "messages": v}}
                        }
                    elif isinstance(v, dict) and "sessions" in v:
                        # Migrar v2 (sessions com listas) → v3 (sessions com dict name+messages)
                        migrated_sessions = {}
                        for sid, session_data in v["sessions"].items():
                            if isinstance(session_data, list):
                                # Formato antigo: lista de mensagens
                                migrated_sessions[sid] = {
                                    "name": f"Chat {sid}",
                                    "messages": session_data
                                }
                            elif isinstance(session_data, dict) and "messages" in session_data:
                                # Já no formato novo
                                migrated_sessions[sid] = session_data
                            else:
                                migrated_sessions[sid] = {
                                    "name": f"Chat {sid}",
                                    "messages": [{
                                        "role": "system",
                                        "content": SYSTEM_PROMPT
                                    }]
                                }
                        user_histories[user_id] = {
                            "current": v.get("current", "1"),
                            "sessions": migrated_sessions
                        }
                    else:
                        user_histories[user_id] = v
            except Exception as e:
                logger.error("Erro ao carregar histórico: %s", e)


def _read_history_file() -> dict:
    """Leitura síncrona do arquivo de histórico (executada em thread)."""
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_history_file(data_str: str) -> None:
    """Escrita atômica do arquivo de histórico (write-tmp-then-rename)."""
    tmp_file = HISTORY_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(data_str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_file, HISTORY_FILE)


async def save_history_now() -> None:
    """Salva o histórico imediatamente (chamado pelo debounce ou shutdown)."""
    try:
        data_str = json.dumps(user_histories, ensure_ascii=False, indent=2)
        await asyncio.to_thread(_write_history_file, data_str)
    except Exception as e:
        logger.error("Erro ao salvar histórico: %s", e)


async def save_history() -> None:
    """Agenda um save com debounce — múltiplas chamadas em sequência resultam em apenas uma escrita."""
    global _save_task
    if _save_task and not _save_task.done():
        _save_task.cancel()

    async def _debounced_save():
        await asyncio.sleep(SAVE_DEBOUNCE_SECONDS)
        async with history_lock:
            await save_history_now()

    _save_task = asyncio.create_task(_debounced_save())


async def save_history_immediate() -> None:
    """Salva o histórico imediatamente, cancelando qualquer debounce pendente."""
    global _save_task
    if _save_task and not _save_task.done():
        _save_task.cancel()
    async with history_lock:
        await save_history_now()


# ==== UTILS ====

def is_user_allowed(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def init_user_session(user_id: int) -> None:
    """Garante que a estrutura de dados do usuário exista."""
    if user_id not in user_histories:
        user_histories[user_id] = {
            "current": "1",
            "sessions": {
                "1": {
                    "name": "Chat 1",
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
                }
            }
        }

    # Se a sessão atual foi apagada e não existe mais, recria a padrão
    curr = user_histories[user_id]["current"]
    if curr not in user_histories[user_id]["sessions"]:
        user_histories[user_id]["sessions"][curr] = {
            "name": f"Chat {curr}",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
        }


def _get_session_messages(user_id: int) -> list:
    """Retorna a lista de mensagens da sessão atual do usuário."""
    curr = user_histories[user_id]["current"]
    return user_histories[user_id]["sessions"][curr]["messages"]


def _sanitize_message_for_history(user_message, caption: str = "") -> str:
    """Remove base64 de imagens antes de persistir no histórico."""
    if isinstance(user_message, list):
        for item in user_message:
            if item.get("type") == "text":
                return f"[Imagem analisada com legenda: '{item.get('text', '')}']"
        return "[Imagem recebida]"
    return user_message


# ==== LLM STREAMING ====

async def get_llm_stream(user_id: int, user_message: Union[str, list]) -> AsyncGenerator[str, None]:
    init_user_session(user_id)
    session_history = _get_session_messages(user_id)

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
        logger.error("Erro ao contatar LM Studio: %s", e)
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

        # Truncar histórico com slice O(1) em vez de loop O(n²)
        if len(session_history) > MAX_HISTORY_LENGTH + 1:
            excess = len(session_history) - (MAX_HISTORY_LENGTH + 1)
            # Garantir que removemos pares completos (user + assistant)
            if excess % 2 != 0:
                excess += 1
            del session_history[1:1 + excess]

        await save_history_immediate()


# ==== FORMATAÇÃO ====

def _format_to_html(text: str) -> str:
    """Converte Markdown básico para HTML do Telegram de forma segura."""
    # Primeiro escapa caracteres HTML especiais
    text = html_lib.escape(text)

    # Code blocks: ```lang\ncode\n``` → <pre><code>code</code></pre>
    def replace_code_block(match: re.Match) -> str:
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


def _smart_split_html(text: str, max_length: int) -> list[str]:
    """Divide texto HTML em partes respeitando o limite, tentando não quebrar tags.
    
    Estratégia: divide pelo texto RAW e formata cada parte separadamente.
    Isso evita cortar tags HTML no meio.
    """
    if len(text) <= max_length:
        return [text]

    # Trabalhar com o texto cru (sem HTML) para dividir de forma segura
    # e depois formatar cada parte
    return []  # fallback handled in caller


def _split_text_safely(raw_text: str, max_length: int) -> list[str]:
    """Divide texto bruto em partes, quebrando em limites naturais (newlines, espaços).
    Cada parte é formatada para HTML separadamente, evitando tags cortadas.
    """
    parts = []
    # Reservar espaço para overhead de formatação HTML (~20%)
    safe_length = int(max_length * 0.8)

    while raw_text:
        if len(raw_text) <= safe_length:
            parts.append(raw_text)
            break

        # Tentar quebrar em uma nova linha
        split_pos = raw_text.rfind('\n', 0, safe_length)
        if split_pos == -1:
            # Tentar quebrar em um espaço
            split_pos = raw_text.rfind(' ', 0, safe_length)
        if split_pos == -1:
            # Forçar quebra
            split_pos = safe_length

        parts.append(raw_text[:split_pos])
        raw_text = raw_text[split_pos:].lstrip('\n')

    return parts


# ==== COMANDOS DO TELEGRAM ====

HELP_TEXT = (
    "🤖 <b>Bot Telegram + LM Studio</b>\n\n"
    "Comandos disponíveis:\n"
    "/start - Inicia a conversa com o bot\n"
    "/help - Mostra esta mensagem de ajuda\n"
    "/new - Cria um novo chat limpo\n"
    "/chats - Lista todos os chats salvos\n"
    "/switch &lt;id&gt; - Alterna para um chat específico\n"
    "/rename &lt;id&gt; &lt;nome&gt; - Renomeia um chat\n"
    "/clear - Limpa a memória do chat atual\n"
    "/delete &lt;id&gt; - Exclui um chat permanentemente\n"
    "/retry - Reenviar a última pergunta para nova resposta\n"
    "/export - Exporta o chat atual como arquivo de texto\n"
    "/status - Mostra diagnóstico do bot e do LM Studio"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user = update.effective_user
    if not is_user_allowed(user.id):
        logger.warning("Acesso negado para o ID %s (%s) no /start", user.id, user.first_name)
        await update.message.reply_text(f"Acesso negado. Seu ID do Telegram é: {user.id}")
        return

    init_user_session(user.id)
    await update.message.reply_text(
        f"Olá, {html_lib.escape(user.first_name)}! Eu sou um bot rodando via LM Studio.\n\n"
        f"{HELP_TEXT}",
        parse_mode=ParseMode.HTML
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user = update.effective_user
    if not is_user_allowed(user.id):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    existing_ids = [int(k) for k in user_histories[user_id]["sessions"].keys() if k.isdigit()]
    next_id = str(max(existing_ids) + 1) if existing_ids else "1"

    user_histories[user_id]["current"] = next_id
    user_histories[user_id]["sessions"][next_id] = {
        "name": f"Chat {next_id}",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
    }
    await save_history()

    await update.message.reply_text(f"✨ Novo chat iniciado! Você está agora na Sessão {next_id}.")


async def list_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    sessions = user_histories[user_id]["sessions"]
    curr = user_histories[user_id]["current"]

    msg_lines = ["📂 <b>Seus chats salvos:</b>\n"]
    for sid, session_data in sessions.items():
        msgs = session_data["messages"]
        chat_name = session_data.get("name", f"Chat {sid}")
        preview = "Vazio"
        for m in msgs:
            if m["role"] == "user":
                content = m["content"] if isinstance(m["content"], str) else "[Imagem]"
                preview = content[:CHAT_PREVIEW_LENGTH] + "..." if len(content) > CHAT_PREVIEW_LENGTH else content
                break

        prefix = "👉" if sid == curr else "💬"
        msg_lines.append(
            f"{prefix} <b>Sessão {sid}</b> — <i>{html_lib.escape(chat_name)}</i>: "
            f"{html_lib.escape(preview)}"
        )

    msg_lines.append("\nUse /switch ID para trocar de chat.")
    await update.message.reply_text("\n".join(msg_lines), parse_mode=ParseMode.HTML)


async def switch_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    if not context.args:
        await update.message.reply_text("Formato incorreto. Use: /switch 1")
        return

    target_id = context.args[0]
    if target_id in user_histories[user_id]["sessions"]:
        user_histories[user_id]["current"] = target_id
        await save_history()
        chat_name = user_histories[user_id]["sessions"][target_id].get("name", f"Chat {target_id}")
        await update.message.reply_text(
            f"🔄 Trocado com sucesso para a Sessão {target_id} (<i>{html_lib.escape(chat_name)}</i>)!",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("❌ Chat não encontrado. Use /chats para ver os IDs disponíveis.")


async def rename_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Renomeia um chat: /rename <id> <novo nome>"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Formato incorreto. Use: /rename 1 Meu Chat Legal")
        return

    target_id = context.args[0]
    new_name = " ".join(context.args[1:])

    if target_id in user_histories[user_id]["sessions"]:
        user_histories[user_id]["sessions"][target_id]["name"] = new_name
        await save_history()
        await update.message.reply_text(
            f"✏️ Sessão {target_id} renomeada para <i>{html_lib.escape(new_name)}</i>.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("❌ Chat não encontrado. Use /chats para ver os IDs disponíveis.")


async def delete_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    if not context.args:
        await update.message.reply_text("Formato incorreto. Use: /delete 1")
        return

    target_id = context.args[0]
    if target_id in user_histories[user_id]["sessions"]:
        was_current = (user_histories[user_id]["current"] == target_id)
        del user_histories[user_id]["sessions"][target_id]

        # Se deletou a sessão atual, redirecionar para outra existente ou criar nova
        if was_current:
            remaining = user_histories[user_id]["sessions"]
            if remaining:
                # Mudar para a primeira sessão disponível
                new_current = next(iter(remaining))
                user_histories[user_id]["current"] = new_current
                await save_history()
                chat_name = remaining[new_current].get("name", f"Chat {new_current}")
                await update.message.reply_text(
                    f"🗑️ Sessão {target_id} apagada. "
                    f"Você foi movido para a Sessão {new_current} (<i>{html_lib.escape(chat_name)}</i>).",
                    parse_mode=ParseMode.HTML
                )
            else:
                # Nenhuma sessão restante — criar uma nova
                user_histories[user_id]["current"] = "1"
                user_histories[user_id]["sessions"]["1"] = {
                    "name": "Chat 1",
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
                }
                await save_history()
                await update.message.reply_text(
                    "🗑️ Sessão apagada. Um novo chat foi criado automaticamente."
                )
        else:
            await save_history()
            await update.message.reply_text(f"🗑️ Sessão {target_id} apagada permanentemente.")
    else:
        await update.message.reply_text("❌ Chat não encontrado.")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    curr = user_histories[user_id]["current"]
    session = user_histories[user_id]["sessions"][curr]
    session["messages"] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await save_history()
    await update.message.reply_text(f"🧹 Memória da Sessão {curr} limpa. Pode começar um novo assunto!")


async def retry_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/retry — Remove a última resposta do assistente e reenvia a última pergunta."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    session_history = _get_session_messages(user_id)

    # Procurar última mensagem do usuário (pulando system prompt)
    last_user_msg = None
    # Remover última resposta do assistant (se existir)
    if len(session_history) >= 2 and session_history[-1]["role"] == "assistant":
        session_history.pop()

    # Remover e capturar última mensagem do user
    if len(session_history) >= 2 and session_history[-1]["role"] == "user":
        last_user_msg = session_history.pop()

    if not last_user_msg:
        await update.message.reply_text("❌ Nenhuma mensagem anterior encontrada para reenviar.")
        return

    await save_history()

    # Reprocessar como se fosse uma nova mensagem
    loading_message = await update.message.reply_text("🔄 Regenerando resposta...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    last_edit_time = time.time()
    final_text = ""

    try:
        async for partial_text in get_llm_stream(user_id, last_user_msg["content"]):
            final_text = partial_text
            current_time = time.time()

            if current_time - last_edit_time > STREAM_EDIT_INTERVAL:
                truncated = partial_text[:MAX_TELEGRAM_MSG_LENGTH - 10]
                formatted_partial = _format_to_html(truncated)
                try:
                    await loading_message.edit_text(formatted_partial + " ✍️", parse_mode=ParseMode.HTML)
                except BadRequest:
                    try:
                        await loading_message.edit_text(truncated + " ✍️")
                    except BadRequest:
                        pass
                last_edit_time = current_time

        if not final_text:
            await loading_message.edit_text("A IA não retornou nenhuma resposta.")
            return

        await _send_final_response(update, loading_message, final_text)

    except Exception as e:
        logger.error("Erro durante /retry: %s", e)
        await update.message.reply_text("Desculpe, ocorreu um erro ao regenerar a resposta.")


async def export_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/export — Exporta o chat atual como arquivo .txt."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    curr = user_histories[user_id]["current"]
    session = user_histories[user_id]["sessions"][curr]
    session_history = session["messages"]
    chat_name = session.get("name", f"Chat {curr}")

    lines = [f"=== {chat_name} (Sessão {curr}) ===\n"]
    for msg in session_history:
        role = msg["role"].upper()
        content = msg["content"] if isinstance(msg["content"], str) else "[Conteúdo multimídia]"
        if role == "SYSTEM":
            lines.append(f"[SYSTEM PROMPT]: {content}\n")
        elif role == "USER":
            lines.append(f"[VOCÊ]: {content}\n")
        elif role == "ASSISTANT":
            lines.append(f"[ASSISTENTE]: {content}\n")
        lines.append("")

    export_text = "\n".join(lines)
    file_bytes = export_text.encode("utf-8")

    from io import BytesIO
    file_obj = BytesIO(file_bytes)
    file_obj.name = f"chat_{curr}_{chat_name.replace(' ', '_')}.txt"

    await update.message.reply_document(
        document=file_obj,
        caption=f"📤 Exportação da Sessão {curr} — {chat_name}"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — Mostra diagnóstico do bot e do LM Studio."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    # Info do bot
    total_users = len(user_histories)
    user_sessions = len(user_histories.get(user_id, {}).get("sessions", {}))
    curr = user_histories[user_id]["current"]
    curr_msgs = len(_get_session_messages(user_id))
    chat_name = user_histories[user_id]["sessions"][curr].get("name", f"Chat {curr}")

    # Checar LM Studio
    lm_status = "❌ Desconectado"
    model_list = []
    latency_ms = "N/A"
    try:
        t0 = time.time()
        models = await client.models.list()
        latency_ms = f"{(time.time() - t0) * 1000:.0f}ms"
        model_list = [m.id for m in models.data]
        lm_status = "✅ Conectado"
    except Exception:
        pass

    models_str = ", ".join(model_list) if model_list else "Nenhum"
    status_text = (
        "📊 <b>Status do Bot</b>\n\n"
        f"<b>LM Studio:</b> {lm_status}\n"
        f"<b>URL:</b> <code>{html_lib.escape(LM_STUDIO_URL)}</code>\n"
        f"<b>Latência:</b> {latency_ms}\n"
        f"<b>Modelo configurado:</b> <code>{html_lib.escape(MODEL_NAME)}</code>\n"
        f"<b>Modelos disponíveis:</b> {html_lib.escape(models_str)}\n"
        f"<b>Temperatura:</b> {TEMPERATURE}\n\n"
        f"<b>Usuários totais:</b> {total_users}\n"
        f"<b>Suas sessões:</b> {user_sessions}\n"
        f"<b>Sessão atual:</b> {curr} — <i>{html_lib.escape(chat_name)}</i> ({curr_msgs} msgs)\n"
        f"<b>Limite de histórico:</b> {MAX_HISTORY_LENGTH} msgs"
    )

    await update.message.reply_text(status_text, parse_mode=ParseMode.HTML)


# ==== HANDLER DE MENSAGENS ====

async def _send_final_response(update: Update, loading_message, final_text: str) -> None:
    """Envia a resposta final formatada, dividindo se necessário."""
    MAX_LENGTH = MAX_TELEGRAM_MSG_LENGTH

    # Dividir o texto cru em partes seguras e formatar cada uma
    parts = _split_text_safely(final_text, MAX_LENGTH)

    for i, part in enumerate(parts):
        formatted = _format_to_html(part)

        # Garantir que a parte formatada não excede o limite
        if len(formatted) > MAX_LENGTH:
            formatted = formatted[:MAX_LENGTH]

        if i == 0:
            # Editar a mensagem "Pensando..."
            try:
                await loading_message.edit_text(formatted, parse_mode=ParseMode.HTML)
            except Exception:
                try:
                    await loading_message.edit_text(part[:MAX_LENGTH])
                except Exception:
                    await loading_message.edit_text("Erro interno ao formatar a mensagem.")
        else:
            # Enviar como mensagens adicionais
            try:
                await update.message.reply_text(formatted, parse_mode=ParseMode.HTML)
            except Exception:
                await update.message.reply_text(part[:MAX_LENGTH])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    user = update.effective_user
    if not is_user_allowed(user.id):
        logger.warning("Tentativa de acesso negado: %s", user.id)
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
            return  # Ignora figurinhas, áudios e vídeos (por enquanto)

    # Typing indicator nativo do Telegram
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    loading_message = await update.message.reply_text("⏳ Pensando...")

    last_edit_time = time.time()
    final_text = ""

    try:
        async for partial_text in get_llm_stream(user.id, message_content):
            final_text = partial_text
            current_time = time.time()

            if current_time - last_edit_time > STREAM_EDIT_INTERVAL:
                # Reenviar typing action periodicamente
                await context.bot.send_chat_action(
                    chat_id=update.effective_chat.id, action=ChatAction.TYPING
                )
                truncated = partial_text[:MAX_TELEGRAM_MSG_LENGTH - 10]
                formatted_partial = _format_to_html(truncated)
                try:
                    await loading_message.edit_text(formatted_partial + " ✍️", parse_mode=ParseMode.HTML)
                except BadRequest:
                    # Fallback sem formatação se HTML parcial for inválido
                    try:
                        await loading_message.edit_text(truncated + " ✍️")
                    except BadRequest:
                        pass
                last_edit_time = current_time

        if not final_text:
            await loading_message.edit_text("A IA não retornou nenhuma resposta.")
            return

        await _send_final_response(update, loading_message, final_text)

    except Exception as e:
        logger.error("Erro durante o handle_message: %s", e)
        try:
            await update.message.reply_text("Desculpe, ocorreu um erro inesperado.")
        except TimedOut:
            logger.warning("Timeout ao enviar mensagem de erro para o usuário %s", user.id)
        except Exception as send_err:
            logger.error("Falha ao enviar mensagem de erro: %s", send_err)


# ==== INICIALIZAÇÃO ====

async def check_lm_studio() -> bool:
    """Health check para verificar se o LM Studio está acessível."""
    try:
        models = await client.models.list()
        model_names = [m.id for m in models.data]
        logger.info("✅ LM Studio conectado. Modelos disponíveis: %s", model_names)
        return True
    except Exception as e:
        logger.warning("⚠️ LM Studio não acessível em %s: %s", LM_STUDIO_URL, e)
        logger.warning("O bot será iniciado mesmo assim. Certifique-se de iniciar o LM Studio antes de enviar mensagens.")
        return False


async def post_init(application) -> None:
    """Executado após a inicialização do bot."""
    await load_history()
    await check_lm_studio()


async def on_shutdown(application) -> None:
    """Garantir que o histórico pendente seja salvo antes de encerrar."""
    global _save_task
    if _save_task and not _save_task.done():
        _save_task.cancel()
    await save_history_now()
    logger.info("Histórico salvo. Bot encerrado com sucesso.")


if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "seu_token_do_telegram_aqui":
        logger.error("Token do Telegram não encontrado! Configure o arquivo .env.")
        exit(1)

    # Timeouts maiores para evitar TimedOut em respostas longas do LM Studio
    request = HTTPXRequest(
        read_timeout=30.0,
        write_timeout=30.0,
        connect_timeout=15.0,
    )

    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
        .post_init(post_init)
        .post_shutdown(on_shutdown)
        .build()
    )

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('new', new_chat))
    application.add_handler(CommandHandler('chats', list_chats))
    application.add_handler(CommandHandler('switch', switch_chat))
    application.add_handler(CommandHandler('rename', rename_chat))
    application.add_handler(CommandHandler('delete', delete_chat))
    application.add_handler(CommandHandler('clear', clear))
    application.add_handler(CommandHandler('retry', retry_command))
    application.add_handler(CommandHandler('export', export_chat))
    application.add_handler(CommandHandler('status', status_command))

    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & (~filters.COMMAND), handle_message))

    logger.info("Bot iniciado! Pressione Ctrl+C para parar.")
    application.run_polling()
