"""
agents/mini/base.py — Classe base para mini agentes.

Mini agentes são tarefas leves que rodam em background após cada resposta
do assistente. Eles fazem chamadas rápidas ao LLM para enriquecer a
experiência sem bloquear o fluxo principal.
"""

import logging
from typing import Optional

from bot.config import client, MODEL_NAME

logger = logging.getLogger(__name__)


class MiniAgent:
    """Mini agente que executa tarefas auxiliares em background."""

    def __init__(
        self,
        name: str,
        emoji: str,
        description: str,
    ):
        self.name = name
        self.emoji = emoji
        self.description = description

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.name}"

    async def quick_llm_call(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 50,
        temperature: float = 0.3,
        model: str = "",
    ) -> Optional[str]:
        """Faz uma chamada rápida ao LLM (sem streaming).

        Usado para tarefas curtas como gerar títulos, resumos, etc.
        Retorna None em caso de erro.
        """
        use_model = model or MODEL_NAME
        try:
            response = await client.chat.completions.create(
                model=use_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
            result = response.choices[0].message.content
            return result.strip() if result else None
        except Exception as e:
            logger.warning("Mini agent %s falhou: %s", self.name, e)
            return None

    def __repr__(self) -> str:
        return f"MiniAgent(name={self.name!r}, emoji={self.emoji!r})"
