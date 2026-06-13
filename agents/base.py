"""
agents/base.py — Classe base para todos os agentes.

Define a interface comum que cada agente especializado implementa,
incluindo system prompt, temperatura, e método de resposta via streaming.
"""

import logging
from typing import AsyncGenerator, Union

from bot.config import client, MODEL_NAME

logger = logging.getLogger(__name__)


class Agent:
    """Agente especializado que processa mensagens com personalidade própria."""

    def __init__(
        self,
        name: str,
        emoji: str,
        system_prompt: str,
        temperature: float = 0.7,
        description: str = "",
    ):
        self.name = name
        self.emoji = emoji
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.description = description

    @property
    def label(self) -> str:
        """Label formatado para exibição: '💻 Coder'."""
        return f"{self.emoji} {self.name}"

    async def respond(
        self,
        messages: list[dict],
        model: str = "",
    ) -> AsyncGenerator[str, None]:
        """Gera resposta via streaming usando o LLM.

        Args:
            messages: Lista de mensagens (já inclui system prompt do agente).
            model: Nome do modelo a usar (padrão: MODEL_NAME do config).

        Yields:
            Texto parcial acumulado da resposta.
        """
        use_model = model or MODEL_NAME

        # Substituir o system prompt original pelo do agente
        agent_messages = list(messages)
        if agent_messages and agent_messages[0]["role"] == "system":
            agent_messages[0] = {
                "role": "system",
                "content": self.system_prompt,
            }
        else:
            agent_messages.insert(0, {
                "role": "system",
                "content": self.system_prompt,
            })

        bot_response = ""
        try:
            response_stream = await client.chat.completions.create(
                model=use_model,
                messages=agent_messages,
                temperature=self.temperature,
                stream=True,
            )

            async for chunk in response_stream:
                delta = chunk.choices[0].delta.content
                if delta is not None:
                    bot_response += delta
                    yield bot_response

        except Exception as e:
            logger.error("Erro no agente %s: %s", self.name, e)
            yield f"Desculpe, ocorreu um erro ao processar sua mensagem: {e}"

    def __repr__(self) -> str:
        return f"Agent(name={self.name!r}, emoji={self.emoji!r}, temp={self.temperature})"
