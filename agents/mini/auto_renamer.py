"""
agents/mini/auto_renamer.py — Mini agente que renomeia chats automaticamente.

Após a primeira troca de mensagem (user + assistant) em um chat novo,
gera um título curto e descritivo baseado no conteúdo da conversa.
Só roda em chats que ainda têm o nome padrão "Chat N".
"""

import logging
import re
from typing import Optional

from agents.mini.base import MiniAgent
from persistence.history import user_histories, save_history

logger = logging.getLogger(__name__)

RENAMER_PROMPT = (
    "Gere um título MUITO curto (máximo 4 palavras) para esta conversa. "
    "O título deve capturar o tema principal. "
    "Responda APENAS com o título, sem aspas, sem pontuação final, sem explicação. "
    "Exemplos: API REST Python, Poema sobre Chuva, Bug no Login, Dicas de Viagem"
)


class AutoRenamer(MiniAgent):
    """Renomeia chats automaticamente após a primeira troca de mensagem."""

    def __init__(self):
        super().__init__(
            name="Auto-Renamer",
            emoji="🏷️",
            description="Renomeia chats automaticamente com um título curto.",
        )

    def _should_rename(self, user_id: int) -> bool:
        """Verifica se o chat atual deve ser renomeado.

        Só renomeia se:
        - O chat existe
        - O nome ainda é o padrão "Chat N"
        - Há pelo menos 1 mensagem do user + 1 do assistant
        """
        if user_id not in user_histories:
            return False

        curr = user_histories[user_id]["current"]
        session = user_histories[user_id]["sessions"].get(curr)
        if not session:
            return False

        chat_name = session.get("name", "")
        # Só renomear se o nome ainda é padrão (Chat 1, Chat 2, etc.)
        if not re.match(r"^Chat \d+$", chat_name):
            return False

        # Verificar se tem pelo menos user + assistant
        messages = session["messages"]
        has_user = any(m["role"] == "user" for m in messages)
        has_assistant = any(m["role"] == "assistant" for m in messages)

        return has_user and has_assistant

    def _get_conversation_preview(self, user_id: int) -> Optional[str]:
        """Retorna um resumo da conversa para o LLM gerar o título."""
        curr = user_histories[user_id]["current"]
        messages = user_histories[user_id]["sessions"][curr]["messages"]

        # Pegar a primeira mensagem do user e a primeira do assistant
        preview_parts = []
        for msg in messages:
            if msg["role"] == "user":
                content = msg["content"] if isinstance(msg["content"], str) else "[Imagem]"
                preview_parts.append(f"Usuário: {content[:200]}")
            elif msg["role"] == "assistant":
                preview_parts.append(f"Assistente: {msg['content'][:200]}")

            if len(preview_parts) >= 2:
                break

        return "\n".join(preview_parts) if preview_parts else None

    async def run(self, user_id: int) -> Optional[str]:
        """Executa o auto-rename se aplicável. Retorna o novo nome ou None."""
        if not self._should_rename(user_id):
            return None

        preview = self._get_conversation_preview(user_id)
        if not preview:
            return None

        new_name = await self.quick_llm_call(
            system_prompt=RENAMER_PROMPT,
            user_prompt=preview,
            max_tokens=20,
            temperature=0.3,
        )

        if not new_name:
            return None

        # Limpar o nome (remover aspas, pontuação, etc.)
        new_name = new_name.strip('"\'.,!?;:')
        # Limitar a 30 chars
        if len(new_name) > 30:
            new_name = new_name[:30]

        if not new_name:
            return None

        # Aplicar o rename
        curr = user_histories[user_id]["current"]
        user_histories[user_id]["sessions"][curr]["name"] = new_name
        await save_history()

        logger.info(
            "🏷️ Auto-Renamer: sessão %s do user %s renomeada para '%s'",
            curr, user_id, new_name,
        )

        return new_name


auto_renamer = AutoRenamer()
