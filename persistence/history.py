"""
persistence/history.py — Gerenciamento de sessões e histórico de conversas.

Responsável por carregar, salvar (com debounce e escrita atômica),
inicializar sessões de usuário e truncar histórico.
"""

import os
import json
import asyncio
import logging
from typing import Optional

from bot.config import (
    SYSTEM_PROMPT,
    MAX_HISTORY_LENGTH,
    HISTORY_FILE,
    SAVE_DEBOUNCE_SECONDS,
)

logger = logging.getLogger(__name__)

# Estado global do histórico
# Estrutura: { user_id: { "current": "1", "sessions": { "1": { "name": "Chat 1", "messages": [...] } } } }
user_histories: dict = {}

# Lock para evitar race conditions
history_lock = asyncio.Lock()

# Debounce para save
_save_task: Optional[asyncio.Task] = None


# ==== I/O ====

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


# ==== CARREGAR / SALVAR ====

async def load_history() -> None:
    """Carrega o histórico salvo no disco e converte se for de versão antiga."""
    global user_histories
    async with history_lock:
        if os.path.exists(HISTORY_FILE):
            try:
                data = await asyncio.to_thread(_read_history_file)
                for k, v in data.items():
                    user_id = int(k)
                    if isinstance(v, list):
                        # Formato v1 (lista simples) → v3
                        user_histories[user_id] = {
                            "current": "1",
                            "sessions": {"1": {"name": "Chat 1", "messages": v}},
                        }
                    elif isinstance(v, dict) and "sessions" in v:
                        # Migrar v2/v3
                        migrated_sessions = {}
                        for sid, session_data in v["sessions"].items():
                            if isinstance(session_data, list):
                                migrated_sessions[sid] = {
                                    "name": f"Chat {sid}",
                                    "messages": session_data,
                                }
                            elif isinstance(session_data, dict) and "messages" in session_data:
                                migrated_sessions[sid] = session_data
                            else:
                                migrated_sessions[sid] = {
                                    "name": f"Chat {sid}",
                                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
                                }
                        user_histories[user_id] = {
                            "current": v.get("current", "1"),
                            "sessions": migrated_sessions,
                        }
                    else:
                        user_histories[user_id] = v
            except Exception as e:
                logger.error("Erro ao carregar histórico: %s", e)


async def save_history_now() -> None:
    """Salva o histórico imediatamente (chamado pelo debounce ou shutdown)."""
    try:
        data_str = json.dumps(user_histories, ensure_ascii=False, indent=2)
        await asyncio.to_thread(_write_history_file, data_str)
    except Exception as e:
        logger.error("Erro ao salvar histórico: %s", e)


async def save_history() -> None:
    """Agenda um save com debounce — múltiplas chamadas resultam em apenas uma escrita."""
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


# ==== SESSÕES ====

def init_user_session(user_id: int) -> None:
    """Garante que a estrutura de dados do usuário exista."""
    if user_id not in user_histories:
        user_histories[user_id] = {
            "current": "1",
            "sessions": {
                "1": {
                    "name": "Chat 1",
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
                }
            },
        }

    curr = user_histories[user_id]["current"]
    if curr not in user_histories[user_id]["sessions"]:
        user_histories[user_id]["sessions"][curr] = {
            "name": f"Chat {curr}",
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
        }


def get_session_messages(user_id: int) -> list:
    """Retorna a lista de mensagens da sessão atual do usuário."""
    curr = user_histories[user_id]["current"]
    return user_histories[user_id]["sessions"][curr]["messages"]


def truncate_history(session_history: list) -> None:
    """Trunca o histórico mantendo o system prompt e respeitando MAX_HISTORY_LENGTH."""
    if len(session_history) > MAX_HISTORY_LENGTH + 1:
        excess = len(session_history) - (MAX_HISTORY_LENGTH + 1)
        if excess % 2 != 0:
            excess += 1
        del session_history[1:1 + excess]
