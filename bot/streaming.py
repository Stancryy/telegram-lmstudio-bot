"""
bot/streaming.py — Lógica de streaming LLM com integração multi-agente e MemPalace.

Gerencia o fluxo completo: roteamento → busca de memórias → streaming → indexação.
"""

import asyncio
import logging
from typing import AsyncGenerator, Optional, Union

from bot.config import (
    client,
    MODEL_NAME,
    TEMPERATURE,
    MEMPALACE_ENABLED,
    MEMPALACE_WING,
    MEMPALACE_RESULTS,
    AGENTS_ENABLED,
)
from persistence.history import (
    user_histories,
    get_session_messages,
    init_user_session,
    save_history,
    save_history_immediate,
    truncate_history,
)
from persistence import mempalace_adapter as mem
from agents import get_agents, is_multi_agent_enabled
from agents.router import route_message
from agents.base import Agent

logger = logging.getLogger(__name__)


async def _safe_background_task(coro) -> None:
    """Wrapper para tasks em background que loga exceções em vez de engoli-las."""
    try:
        await coro
    except Exception as e:
        logger.error("Background task falhou: %s", e, exc_info=True)

# Estado: agente forçado por usuário (via /agent <nome>)
_forced_agents: dict[int, Optional[str]] = {}


def set_forced_agent(user_id: int, agent_name: Optional[str]) -> None:
    """Define um agente forçado para o próximo uso do usuário."""
    _forced_agents[user_id] = agent_name


def get_forced_agent(user_id: int) -> Optional[str]:
    """Retorna o agente forçado para o usuário (ou None)."""
    return _forced_agents.get(user_id)


def clear_forced_agent(user_id: int) -> None:
    """Remove o agente forçado após o uso."""
    _forced_agents.pop(user_id, None)


def sanitize_message_for_history(user_message) -> str:
    """Remove base64 de imagens antes de persistir no histórico."""
    if isinstance(user_message, list):
        # Contar quantas imagens existem
        image_count = sum(1 for item in user_message if item.get("type") == "image_url")
        # Extrair o texto/caption
        text_parts = [item.get("text", "") for item in user_message if item.get("type") == "text"]
        caption = text_parts[0] if text_parts else ""

        if image_count > 1:
            return f"[{image_count} imagens analisadas com legenda: '{caption}']"
        elif image_count == 1:
            return f"[Imagem analisada com legenda: '{caption}']"
        return "[Conteúdo multimídia recebido]"
    return user_message


async def get_llm_stream(
    user_id: int,
    user_message: Union[str, list],
) -> AsyncGenerator[tuple[str, Optional[Agent]], None]:
    """Processa uma mensagem do usuário com roteamento de agentes e streaming.

    Yields:
        Tupla (texto_parcial_acumulado, agente_selecionado).
        O agente é None até ser decidido, depois é o Agent usado.
    """
    init_user_session(user_id)
    session_history = get_session_messages(user_id)

    # Adiciona a mensagem do usuário ao histórico
    session_history.append({"role": "user", "content": user_message})

    # Sanitizar para persistência (sem base64)
    sanitized = sanitize_message_for_history(user_message)
    if sanitized != user_message:
        original_content = session_history[-1]["content"]
        session_history[-1]["content"] = sanitized
        await save_history()
        session_history[-1]["content"] = original_content
    else:
        await save_history()

    # === MemPalace: buscar memórias relevantes ===
    messages_to_send = list(session_history)
    if MEMPALACE_ENABLED and mem.is_available():
        query_text = sanitized if isinstance(user_message, list) else user_message
        try:
            memories = await mem.search_memories(
                query=query_text,
                wing=MEMPALACE_WING,
                n_results=MEMPALACE_RESULTS,
            )
            if memories:
                context_text = mem.format_memories_for_context(memories)
                if context_text:
                    memory_msg = {"role": "system", "content": context_text}
                    messages_to_send.insert(1, memory_msg)
                    logger.info(
                        "🏛️ MemPalace: %d memória(s) injetada(s) para user %s",
                        len(memories), user_id,
                    )
        except Exception as e:
            logger.warning("MemPalace: falha na busca de memórias: %s", e)

    # === Roteamento de agentes ===
    selected_agent: Optional[Agent] = None

    if is_multi_agent_enabled():
        query_text = sanitized if isinstance(user_message, list) else user_message
        forced = get_forced_agent(user_id)
        selected_agent = await route_message(
            user_message=query_text,
            available_agents=get_agents(),
            forced_agent=forced,
        )
        # Limpar o agente forçado após uso (volta para automático)
        if forced:
            clear_forced_agent(user_id)

    # === Streaming da resposta ===
    bot_response = ""
    try:
        if selected_agent:
            # Usar o agente selecionado
            async for partial in selected_agent.respond(messages_to_send):
                bot_response = partial
                yield (bot_response, selected_agent)
        else:
            # Modo sem agentes — usar configuração padrão
            response_stream = await client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages_to_send,
                temperature=TEMPERATURE,
                stream=True,
            )
            async for chunk in response_stream:
                delta = chunk.choices[0].delta.content
                if delta is not None:
                    bot_response += delta
                    yield (bot_response, None)

    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ("connect", "refused", "unreachable", "timeout", "closed")):
            logger.error("LM Studio inacessível: %s", e)
            yield ("⚠️ O LM Studio parece estar offline ou inacessível. "
                   "Verifique se ele está rodando e tente novamente.", selected_agent)
        else:
            logger.error("Erro ao contatar LM Studio: %s", e)
            yield (f"Desculpe, ocorreu um erro ao processar sua mensagem: {e}", selected_agent)
    finally:
        # Persistir mensagem e resposta
        if isinstance(user_message, list):
            session_history[-1]["content"] = sanitized

        if bot_response:
            session_history.append({"role": "assistant", "content": bot_response})
        else:
            session_history.pop()

        truncate_history(session_history)
        await save_history_immediate()

        # MemPalace: indexar conversa em background
        if MEMPALACE_ENABLED and mem.is_available() and bot_response:
            curr = user_histories[user_id]["current"]
            asyncio.create_task(
                _safe_background_task(
                    mem.mine_conversation(
                        user_id=user_id,
                        session_id=curr,
                        messages=session_history,
                        wing=MEMPALACE_WING,
                    )
                )
            )
