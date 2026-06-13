"""
agents/router.py — Roteador inteligente que decide qual agente usar.

Faz uma chamada rápida ao LLM com prompt curto para classificar
a mensagem do usuário e retorna o agente mais adequado.
"""

import logging
from typing import Optional

from bot.config import client, ROUTER_MODEL
from agents.base import Agent

logger = logging.getLogger(__name__)

# Prompt do roteador — deve ser curto e direto para resposta rápida
ROUTER_PROMPT = """Você é um classificador de mensagens. Analise a mensagem do usuário e responda APENAS com uma das categorias abaixo, sem explicação:

- CODER — Se a mensagem é sobre programação, código, debugging, APIs, frameworks, arquitetura de software
- RESEARCHER — Se a mensagem pede explicação detalhada, comparação, fatos, definições, "como funciona", "o que é"
- CREATIVE — Se a mensagem pede escrita criativa, poemas, histórias, brainstorming, ideias originais, nomes criativos
- ANALYST — Se a mensagem envolve matemática, cálculos, análise de dados, lógica, estatística, probabilidade
- GENERAL — Para conversas casuais, saudações, perguntas simples, ou qualquer coisa que não se encaixe acima

Responda APENAS com a categoria (ex: CODER). Nada mais."""


async def route_message(
    user_message: str,
    available_agents: dict[str, "Agent"],
    forced_agent: Optional[str] = None,
) -> "Agent":
    """Determina qual agente deve responder à mensagem.

    Args:
        user_message: Texto da mensagem do usuário.
        available_agents: Dict de agentes disponíveis {name_lower: Agent}.
        forced_agent: Se definido, força o uso deste agente (bypass do roteador).

    Returns:
        O Agent selecionado para responder.
    """
    # Se o usuário forçou um agente específico
    if forced_agent and forced_agent in available_agents:
        agent = available_agents[forced_agent]
        logger.info("🧠 Router: agente forçado → %s", agent.label)
        return agent

    # Fallback agent
    fallback = available_agents.get("general", list(available_agents.values())[0])

    try:
        response = await client.chat.completions.create(
            model=ROUTER_MODEL,
            messages=[
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,  # Baixa para resposta determinística
            max_tokens=20,    # Só precisa de uma palavra
            stream=False,
        )

        category = response.choices[0].message.content.strip().upper()

        # Mapear categoria para agente
        category_map = {
            "CODER": "coder",
            "RESEARCHER": "researcher",
            "CREATIVE": "creative",
            "ANALYST": "analyst",
            "GENERAL": "general",
        }

        agent_key = category_map.get(category, "general")

        if agent_key in available_agents:
            agent = available_agents[agent_key]
            logger.info("🧠 Router: %s → %s", category, agent.label)
            return agent
        else:
            logger.info("🧠 Router: agente %s desabilitado, usando fallback", agent_key)
            return fallback

    except Exception as e:
        logger.warning("🧠 Router: falha na classificação (%s), usando General", e)
        return fallback
