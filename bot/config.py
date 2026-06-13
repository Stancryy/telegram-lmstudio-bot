"""
bot/config.py — Configuração centralizada do bot.

Carrega variáveis de ambiente, define constantes e inicializa o client OpenAI.
"""

import os
import logging
from typing import Optional
from dotenv import load_dotenv
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

# ==== TELEGRAM ====
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ==== LM STUDIO ====
LM_STUDIO_URL: str = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")
MODEL_NAME: str = os.getenv("MODEL_NAME", "local-model")
SYSTEM_PROMPT: str = os.getenv("SYSTEM_PROMPT", "")

try:
    TEMPERATURE: float = float(os.getenv("TEMPERATURE", "0.7"))
    if not 0.0 <= TEMPERATURE <= 2.0:
        raise ValueError("TEMPERATURE fora do intervalo válido (0.0 - 2.0)")
except (ValueError, TypeError):
    logger.warning("TEMPERATURE inválido no .env, usando valor padrão 0.7")
    TEMPERATURE = 0.7

# ==== SEGURANÇA ====
ALLOWED_USER_IDS_ENV: str = os.getenv("ALLOWED_USER_IDS", "")
if ALLOWED_USER_IDS_ENV.strip():
    ALLOWED_USER_IDS: set[int] = {
        int(x.strip()) for x in ALLOWED_USER_IDS_ENV.split(",") if x.strip()
    }
else:
    ALLOWED_USER_IDS: set[int] = set()

# ==== HISTÓRICO ====
MAX_HISTORY_LENGTH: int = int(os.getenv("MAX_HISTORY_LENGTH", "800"))
HISTORY_FILE: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "histories.json",
)

# ==== TELEGRAM LIMITS ====
MAX_TELEGRAM_MSG_LENGTH: int = 4096
STREAM_EDIT_INTERVAL: float = 1.0  # segundos entre edições durante streaming
CHAT_PREVIEW_LENGTH: int = 35
SAVE_DEBOUNCE_SECONDS: float = 2.0

# ==== MEMPALACE ====
MEMPALACE_ENABLED: bool = os.getenv("MEMPALACE_ENABLED", "true").lower() in (
    "true", "1", "yes",
)
MEMPALACE_RESULTS: int = int(os.getenv("MEMPALACE_RESULTS", "3"))
MEMPALACE_WING: str = os.getenv("MEMPALACE_WING", "telegram_bot")

# ==== MULTI-AGENT ====
AGENTS_ENABLED: bool = os.getenv("AGENTS_ENABLED", "true").lower() in (
    "true", "1", "yes",
)
ROUTER_MODEL: str = os.getenv("ROUTER_MODEL", MODEL_NAME)
AGENT_CODER_ENABLED: bool = os.getenv("AGENT_CODER_ENABLED", "true").lower() in (
    "true", "1", "yes",
)
AGENT_RESEARCHER_ENABLED: bool = os.getenv("AGENT_RESEARCHER_ENABLED", "true").lower() in (
    "true", "1", "yes",
)
AGENT_CREATIVE_ENABLED: bool = os.getenv("AGENT_CREATIVE_ENABLED", "true").lower() in (
    "true", "1", "yes",
)
AGENT_ANALYST_ENABLED: bool = os.getenv("AGENT_ANALYST_ENABLED", "true").lower() in (
    "true", "1", "yes",
)

# ==== CLIENT OPENAI ====
client = AsyncOpenAI(
    base_url=LM_STUDIO_URL,
    api_key="lm-studio",
)


def is_user_allowed(user_id: int) -> bool:
    """Verifica se um usuário tem permissão para usar o bot."""
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS
