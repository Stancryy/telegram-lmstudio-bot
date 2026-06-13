"""
bot/handlers.py — Handlers de comandos e mensagens do Telegram.

Contém todos os handlers de comando (/start, /help, etc.)
e o handler principal de mensagens de texto/imagem.
"""

import base64
import time
import logging
import html as html_lib
from io import BytesIO
import asyncio
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.error import BadRequest, TimedOut
from telegram.ext import ContextTypes

from bot.config import (
    MAX_TELEGRAM_MSG_LENGTH,
    STREAM_EDIT_INTERVAL,
    CHAT_PREVIEW_LENGTH,
    SYSTEM_PROMPT,
    MEMPALACE_ENABLED,
    MEMPALACE_WING,
    LM_STUDIO_URL,
    MODEL_NAME,
    TEMPERATURE,
    MAX_HISTORY_LENGTH,
    is_user_allowed,
    client,
)
from bot.formatting import format_to_html, split_text_safely
from bot.streaming import get_llm_stream, set_forced_agent
from persistence.history import (
    user_histories,
    init_user_session,
    get_session_messages,
    save_history,
    save_history_immediate,
)
from persistence import mempalace_adapter as mem
from agents import get_agents, is_multi_agent_enabled
from agents.base import Agent
from agents.mini.auto_renamer import auto_renamer

logger = logging.getLogger(__name__)


# ==== HELP TEXT ====

def _build_help_text() -> str:
    """Gera o texto de ajuda dinamicamente baseado nas features ativas."""
    lines = [
        "🤖 <b>Bot Telegram + LM Studio</b>\n",
        "Comandos disponíveis:",
        "/start - Inicia a conversa com o bot",
        "/help - Mostra esta mensagem de ajuda",
        "/new - Cria um novo chat limpo",
        "/chats - Lista todos os chats salvos",
        "/switch &lt;id&gt; - Alterna para um chat específico",
        "/rename &lt;id&gt; &lt;nome&gt; - Renomeia um chat",
        "/clear - Limpa a memória do chat atual",
        "/delete &lt;id&gt; - Exclui um chat permanentemente",
        "/retry - Reenviar a última pergunta para nova resposta",
        "/export - Exporta o chat atual como arquivo de texto",
        "/status - Mostra diagnóstico do bot e do LM Studio",
    ]

    if is_multi_agent_enabled():
        lines.append("")
        lines.append("🤖 <b>Multi-Agente</b>")
        lines.append("/agents - Lista os agentes disponíveis")
        lines.append("/agent &lt;nome&gt; - Força um agente para a próxima mensagem")

    if MEMPALACE_ENABLED:
        lines.append("")
        lines.append("🏛️ <b>Memória de Longo Prazo (MemPalace)</b>")
        lines.append("/remember &lt;query&gt; - Busca nas memórias passadas")
        lines.append("/memory - Status da memória de longo prazo")
        lines.append("/forget - Apaga todas as memórias armazenadas")

    return "\n".join(lines)


# ==== COMANDOS BÁSICOS ====

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
        f"{_build_help_text()}",
        parse_mode=ParseMode.HTML,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not is_user_allowed(update.effective_user.id):
        return
    await update.message.reply_text(_build_help_text(), parse_mode=ParseMode.HTML)


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
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
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
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("❌ Chat não encontrado. Use /chats para ver os IDs disponíveis.")


async def rename_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            parse_mode=ParseMode.HTML,
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

        if was_current:
            remaining = user_histories[user_id]["sessions"]
            if remaining:
                new_current = next(iter(remaining))
                user_histories[user_id]["current"] = new_current
                await save_history()
                chat_name = remaining[new_current].get("name", f"Chat {new_current}")
                await update.message.reply_text(
                    f"🗑️ Sessão {target_id} apagada. "
                    f"Você foi movido para a Sessão {new_current} (<i>{html_lib.escape(chat_name)}</i>).",
                    parse_mode=ParseMode.HTML,
                )
            else:
                user_histories[user_id]["current"] = "1"
                user_histories[user_id]["sessions"]["1"] = {
                    "name": "Chat 1",
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
                }
                await save_history()
                await update.message.reply_text("🗑️ Sessão apagada. Um novo chat foi criado automaticamente.")
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


# ==== RETRY ====

async def retry_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    session_history = get_session_messages(user_id)

    last_user_msg = None
    if len(session_history) >= 2 and session_history[-1]["role"] == "assistant":
        session_history.pop()
    if len(session_history) >= 2 and session_history[-1]["role"] == "user":
        last_user_msg = session_history.pop()

    if not last_user_msg:
        await update.message.reply_text("❌ Nenhuma mensagem anterior encontrada para reenviar.")
        return

    await save_history()

    loading_message = await update.message.reply_text("🔄 Regenerando resposta...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    last_edit_time = time.time()
    final_text = ""
    final_agent: Optional[Agent] = None

    try:
        async for partial_text, agent in get_llm_stream(user_id, last_user_msg["content"]):
            final_text = partial_text
            final_agent = agent
            current_time = time.time()

            if current_time - last_edit_time > STREAM_EDIT_INTERVAL:
                truncated = partial_text[:MAX_TELEGRAM_MSG_LENGTH - 10]
                formatted_partial = format_to_html(truncated)
                agent_label = f"{agent.label}: " if agent else ""
                try:
                    await loading_message.edit_text(
                        f"{agent_label}{formatted_partial} ✍️",
                        parse_mode=ParseMode.HTML,
                    )
                except BadRequest:
                    try:
                        await loading_message.edit_text(truncated + " ✍️")
                    except BadRequest:
                        pass
                last_edit_time = current_time

        if not final_text:
            await loading_message.edit_text("A IA não retornou nenhuma resposta.")
            return

        await _send_final_response(update, loading_message, final_text, final_agent)

        # Executar mini agentes em background
        asyncio.create_task(_trigger_mini_agents(update, user_id, final_text))

    except Exception as e:
        logger.error("Erro durante /retry: %s", e)
        try:
            await update.message.reply_text("Desculpe, ocorreu um erro ao regenerar a resposta.")
        except TimedOut:
            logger.warning("Timeout ao enviar mensagem de erro no /retry")


# ==== EXPORT ====

async def export_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    file_obj = BytesIO(file_bytes)
    file_obj.name = f"chat_{curr}_{chat_name.replace(' ', '_')}.txt"

    await update.message.reply_document(
        document=file_obj,
        caption=f"📤 Exportação da Sessão {curr} — {chat_name}",
    )


# ==== STATUS ====

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return
    init_user_session(user_id)

    total_users = len(user_histories)
    user_sessions = len(user_histories.get(user_id, {}).get("sessions", {}))
    curr = user_histories[user_id]["current"]
    curr_msgs = len(get_session_messages(user_id))
    chat_name = user_histories[user_id]["sessions"][curr].get("name", f"Chat {curr}")

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

    # Info de agentes
    agents_info = ""
    if is_multi_agent_enabled():
        agents = get_agents()
        agent_list = " | ".join([a.label for a in agents.values()])
        agents_info = f"\n\n<b>🤖 Multi-Agente:</b> ✅ Ativo\n<b>Agentes:</b> {agent_list}"

    # Info do MemPalace
    mem_info = ""
    if MEMPALACE_ENABLED:
        mem_status = "✅ Ativo" if mem.is_available() else "❌ Indisponível"
        mem_info = f"\n\n<b>🏛️ MemPalace:</b> {mem_status}"

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
        f"{agents_info}{mem_info}"
    )

    await update.message.reply_text(status_text, parse_mode=ParseMode.HTML)


# ==== COMANDOS MULTI-AGENTE ====

async def agents_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/agents — Lista os agentes disponíveis."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return

    if not is_multi_agent_enabled():
        await update.message.reply_text(
            "🤖 Sistema multi-agente desabilitado. Configure <code>AGENTS_ENABLED=true</code> no .env.",
            parse_mode=ParseMode.HTML,
        )
        return

    agents = get_agents()
    lines = ["🤖 <b>Agentes Disponíveis:</b>\n"]

    for key, agent in agents.items():
        lines.append(
            f"{agent.emoji} <b>{agent.name}</b> — {html_lib.escape(agent.description)}\n"
            f"   Temperatura: {agent.temperature} | Comando: <code>/agent {key}</code>"
        )

    lines.append("\n<i>O roteador escolhe automaticamente o melhor agente para cada mensagem.</i>")
    lines.append("Use <code>/agent auto</code> para voltar ao roteamento automático.")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/agent <nome> — Força um agente específico para a próxima mensagem."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return

    if not is_multi_agent_enabled():
        await update.message.reply_text("🤖 Sistema multi-agente desabilitado.")
        return

    if not context.args:
        await update.message.reply_text(
            "Formato: /agent &lt;nome&gt;\n"
            "Exemplos: /agent coder, /agent creative, /agent auto",
            parse_mode=ParseMode.HTML,
        )
        return

    agent_name = context.args[0].lower()

    if agent_name == "auto":
        set_forced_agent(user_id, None)
        await update.message.reply_text("🔄 Roteamento automático restaurado!")
        return

    agents = get_agents()
    if agent_name in agents:
        agent = agents[agent_name]
        set_forced_agent(user_id, agent_name)
        await update.message.reply_text(
            f"✅ Próxima mensagem será processada por {agent.label}.\n"
            f"<i>Use /agent auto para voltar ao roteamento automático.</i>",
            parse_mode=ParseMode.HTML,
        )
    else:
        available = ", ".join(agents.keys())
        await update.message.reply_text(
            f"❌ Agente não encontrado. Disponíveis: <code>{available}</code>",
            parse_mode=ParseMode.HTML,
        )


# ==== COMANDOS MEMPALACE ====

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return

    if not MEMPALACE_ENABLED:
        await update.message.reply_text("🏛️ MemPalace está desabilitado.")
        return

    if not mem.is_available():
        await update.message.reply_text("🏛️ MemPalace não está disponível.")
        return

    if not context.args:
        await update.message.reply_text("Formato: /remember sua busca aqui")
        return

    query = " ".join(context.args)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    memories = await mem.search_memories(query=query, wing=MEMPALACE_WING, n_results=5)

    if not memories:
        await update.message.reply_text(
            f'🏛️ Nenhuma memória encontrada para: "{html_lib.escape(query)}"',
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [f'🏛️ <b>Memórias encontradas para:</b> "{html_lib.escape(query)}"\n']
    for i, m in enumerate(memories, 1):
        sim = m.get("similarity", 0)
        text = m.get("text", "").strip()
        wing = html_lib.escape(m.get("wing", "?"))
        room = html_lib.escape(m.get("room", "?"))

        if len(text) > 200:
            text = text[:200] + "..."

        lines.append(
            f"<b>[{i}]</b> {wing}/{room} — <i>relevância: {sim:.0%}</i>\n"
            f"<code>{html_lib.escape(text)}</code>\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def memory_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return

    if not MEMPALACE_ENABLED:
        await update.message.reply_text(
            "🏛️ <b>MemPalace</b>: Desabilitado\n\n"
            "Configure <code>MEMPALACE_ENABLED=true</code> no .env.",
            parse_mode=ParseMode.HTML,
        )
        return

    status = await mem.get_memory_status()

    if not status.get("available"):
        reason = status.get("reason", "Desconhecido")
        await update.message.reply_text(
            f"🏛️ <b>MemPalace</b>: Indisponível\nMotivo: {html_lib.escape(reason)}",
            parse_mode=ParseMode.HTML,
        )
        return

    total = status.get("total_drawers", 0)
    palace_path = html_lib.escape(status.get("palace_path", "?"))
    wings = status.get("wings", {})

    wings_text = "Nenhum"
    if wings:
        wings_lines = [f"  • <code>{html_lib.escape(w)}</code>: {c} drawer(s)" for w, c in wings.items()]
        wings_text = "\n".join(wings_lines)

    msg = (
        "🏛️ <b>Status da Memória de Longo Prazo</b>\n\n"
        f"<b>Status:</b> ✅ Ativo\n"
        f"<b>Wing:</b> <code>{html_lib.escape(MEMPALACE_WING)}</code>\n"
        f"<b>Total de memórias:</b> {total}\n"
        f"<b>Palace:</b> <code>{palace_path}</code>\n\n"
        f"<b>Wings armazenadas:</b>\n{wings_text}"
    )

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_user_allowed(user_id):
        return

    if not MEMPALACE_ENABLED or not mem.is_available():
        await update.message.reply_text("🏛️ MemPalace não está ativo.")
        return

    result = await mem.forget_memories(wing=MEMPALACE_WING)

    if result.get("success"):
        deleted = result.get("deleted_drawers", 0)
        remaining = result.get("remaining_drawers", 0)
        await update.message.reply_text(
            f"🗑️ <b>{deleted}</b> memória(s) apagada(s) do wing <code>{html_lib.escape(MEMPALACE_WING)}</code>.\n"
            f"Memórias restantes no palace: <b>{remaining}</b>",
            parse_mode=ParseMode.HTML,
        )
    else:
        reason = result.get("reason", "Erro desconhecido")
        await update.message.reply_text(f"❌ Falha ao apagar memórias: {reason}")


# ==== HANDLER PRINCIPAL DE MENSAGENS ====

async def _send_final_response(
    update: Update,
    loading_message,
    final_text: str,
    agent: Optional[Agent] = None,
) -> None:
    """Envia a resposta final formatada, dividindo se necessário."""
    MAX_LENGTH = MAX_TELEGRAM_MSG_LENGTH

    # Prefixo do agente
    agent_prefix = f"{agent.label}: " if agent else ""

    parts = split_text_safely(final_text, MAX_LENGTH)

    for i, part in enumerate(parts):
        formatted = format_to_html(part)

        # Adicionar prefixo do agente apenas na primeira parte
        if i == 0 and agent_prefix:
            formatted = f"<b>{html_lib.escape(agent_prefix)}</b>\n{formatted}"

        if len(formatted) > MAX_LENGTH:
            formatted = formatted[:MAX_LENGTH]

        if i == 0:
            try:
                await loading_message.edit_text(formatted, parse_mode=ParseMode.HTML)
            except Exception:
                try:
                    await loading_message.edit_text(part[:MAX_LENGTH])
                except Exception:
                    await loading_message.edit_text("Erro interno ao formatar a mensagem.")
        else:
            try:
                await update.message.reply_text(formatted, parse_mode=ParseMode.HTML)
            except Exception:
                await update.message.reply_text(part[:MAX_LENGTH])


async def _trigger_mini_agents(update: Update, user_id: int, final_text: str) -> None:
    """Executa mini agentes e envia os resultados adicionais."""
    # Auto-renamer (silencioso)
    await auto_renamer.run(user_id)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler principal para mensagens de texto e imagem."""
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
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
        ]
    else:
        message_content = update.message.text
        if not message_content:
            return

    # Typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    loading_message = await update.message.reply_text("⏳ Pensando...")

    last_edit_time = time.time()
    final_text = ""
    final_agent: Optional[Agent] = None

    try:
        async for partial_text, agent in get_llm_stream(user.id, message_content):
            final_text = partial_text
            final_agent = agent
            current_time = time.time()

            if current_time - last_edit_time > STREAM_EDIT_INTERVAL:
                await context.bot.send_chat_action(
                    chat_id=update.effective_chat.id, action=ChatAction.TYPING,
                )
                truncated = partial_text[:MAX_TELEGRAM_MSG_LENGTH - 10]
                formatted_partial = format_to_html(truncated)
                agent_label = f"{agent.emoji} " if agent else ""
                try:
                    await loading_message.edit_text(
                        f"{agent_label}{formatted_partial} ✍️",
                        parse_mode=ParseMode.HTML,
                    )
                except BadRequest:
                    try:
                        await loading_message.edit_text(truncated + " ✍️")
                    except BadRequest:
                        pass
                last_edit_time = current_time

        if not final_text:
            await loading_message.edit_text("A IA não retornou nenhuma resposta.")
            return

        await _send_final_response(update, loading_message, final_text, final_agent)

        # Executar mini agentes em background
        asyncio.create_task(_trigger_mini_agents(update, user.id, final_text))

    except Exception as e:
        logger.error("Erro durante o handle_message: %s", e)
        try:
            await update.message.reply_text("Desculpe, ocorreu um erro inesperado.")
        except TimedOut:
            logger.warning("Timeout ao enviar mensagem de erro para o usuário %s", user.id)
        except Exception as send_err:
            logger.error("Falha ao enviar mensagem de erro: %s", send_err)
