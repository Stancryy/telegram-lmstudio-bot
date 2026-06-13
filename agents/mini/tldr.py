"""
agents/mini/tldr.py — Mini agente que gera resumo de respostas longas.

Se a resposta do assistente for maior que um limite configurável,
gera um TL;DR automático de 1-2 frases para adicionar ao final.
"""

import logging
from typing import Optional

from agents.mini.base import MiniAgent

logger = logging.getLogger(__name__)

# Limite mínimo de caracteres para gerar TL;DR
TLDR_MIN_LENGTH = 800

TLDR_PROMPT = (
    "Gere um resumo MUITO curto (1-2 frases, máximo 100 caracteres) da resposta abaixo. "
    "O resumo deve capturar o ponto principal. "
    "Responda APENAS com o resumo, sem prefixos como 'Resumo:' ou 'TL;DR:'."
)


class TldrAgent(MiniAgent):
    """Gera resumo automático de respostas longas."""

    def __init__(self):
        super().__init__(
            name="TL;DR",
            emoji="📝",
            description="Gera resumo curto de respostas longas.",
        )

    async def run(self, assistant_response: str) -> Optional[str]:
        """Gera TL;DR se a resposta for longa o suficiente. Retorna o resumo ou None."""
        if len(assistant_response) < TLDR_MIN_LENGTH:
            return None

        summary = await self.quick_llm_call(
            system_prompt=TLDR_PROMPT,
            user_prompt=assistant_response[:2000],  # Limitar input para velocidade
            max_tokens=60,
            temperature=0.3,
        )

        if not summary:
            return None

        summary = summary.strip('"\'')
        logger.info("📝 TL;DR: resumo gerado (%d chars)", len(summary))
        return summary


tldr_agent = TldrAgent()
