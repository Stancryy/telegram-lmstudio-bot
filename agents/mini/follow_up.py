"""
agents/mini/follow_up.py — Mini agente que sugere perguntas de follow-up.

Gera 2 ou 3 perguntas curtas que o usuário poderia fazer em seguida,
baseado na resposta do assistente, para manter o engajamento.
"""

import logging
from typing import Optional

from agents.mini.base import MiniAgent

logger = logging.getLogger(__name__)

FOLLOWUP_PROMPT = (
    "Baseado na conversa, sugira 2 perguntas curtas de follow-up "
    "que o usuário poderia fazer a seguir para se aprofundar no assunto. "
    "Responda apenas com as perguntas, uma por linha, começando com '💡 '."
)


class FollowUpAgent(MiniAgent):
    """Sugere perguntas de follow-up após a resposta."""

    def __init__(self):
        super().__init__(
            name="Follow-up",
            emoji="💡",
            description="Sugere perguntas para continuar a conversa.",
        )

    async def run(self, assistant_response: str) -> Optional[str]:
        """Gera sugestões de perguntas baseadas na resposta."""
        # Se a resposta for muito curta, talvez não precise de follow-up
        if len(assistant_response) < 100:
            return None

        suggestions = await self.quick_llm_call(
            system_prompt=FOLLOWUP_PROMPT,
            user_prompt=assistant_response[:2000],
            max_tokens=100,
            temperature=0.6,
        )

        if not suggestions:
            return None

        # Formatar caso o LLM não tenha seguido exatamente a instrução
        lines = [line.strip() for line in suggestions.split('\n') if line.strip()]
        formatted_lines = []
        for line in lines:
            # Remover números ou marcadores de lista, adicionar o emoji
            clean_line = line.lstrip('1234567890.-*• ')
            if not clean_line.startswith('💡'):
                clean_line = f"💡 {clean_line}"
            formatted_lines.append(clean_line)
            
            if len(formatted_lines) >= 3:
                break

        return "\n".join(formatted_lines) if formatted_lines else None


follow_up_agent = FollowUpAgent()
