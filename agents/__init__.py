"""
agents/__init__.py — Registry de agentes disponíveis.

Carrega os agentes habilitados baseado nas configurações do .env
e fornece funções para acessá-los.
"""

import logging
from typing import Optional

from bot.config import (
    AGENTS_ENABLED,
    AGENT_CODER_ENABLED,
    AGENT_RESEARCHER_ENABLED,
    AGENT_CREATIVE_ENABLED,
    AGENT_ANALYST_ENABLED,
)
from agents.base import Agent
from agents.general import general_agent
from agents.coder import coder_agent
from agents.researcher import researcher_agent
from agents.creative import creative_agent
from agents.analyst import analyst_agent

logger = logging.getLogger(__name__)

# Registry de todos os agentes disponíveis
_agents: dict[str, Agent] = {}


def init_agents() -> dict[str, Agent]:
    """Inicializa e registra os agentes habilitados."""
    global _agents
    _agents = {}

    # General está sempre disponível
    _agents["general"] = general_agent

    if AGENT_CODER_ENABLED:
        _agents["coder"] = coder_agent

    if AGENT_RESEARCHER_ENABLED:
        _agents["researcher"] = researcher_agent

    if AGENT_CREATIVE_ENABLED:
        _agents["creative"] = creative_agent

    if AGENT_ANALYST_ENABLED:
        _agents["analyst"] = analyst_agent

    agent_names = [a.label for a in _agents.values()]
    logger.info("🤖 Agentes carregados: %s", ", ".join(agent_names))

    return _agents


def get_agents() -> dict[str, Agent]:
    """Retorna o registry de agentes."""
    if not _agents:
        init_agents()
    return _agents


def get_agent(name: str) -> Optional[Agent]:
    """Retorna um agente pelo nome (case-insensitive)."""
    agents = get_agents()
    return agents.get(name.lower())


def is_multi_agent_enabled() -> bool:
    """Verifica se o sistema multi-agente está habilitado."""
    return AGENTS_ENABLED
