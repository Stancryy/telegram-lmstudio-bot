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
from bot.config import ROUTER_MODEL

logger = logging.getLogger(__name__)

RENAMER_PROMPT = (
    "Gere um título MUITO curto (máximo 4 palavras) para esta conversa. "
    "O título deve capturar o tema principal. "
    "REGRA CRÍTICA: VOCÊ ESTÁ PROIBIDO DE PENSAR OU RACIOCINAR. "
    "Não use tags <think>. Não explique nada. "
    "Sua primeira e única palavra gerada DEVE SER 'TITULO:'. "
    "O formato EXATO da sua resposta deve ser:\n"
    "TITULO: [seu titulo aqui]\n\n"
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
            max_tokens=30,
            temperature=0.0,
            model=ROUTER_MODEL,
            assistant_prefill="TITULO:",
            stop=["\n"]
        )

        if not new_name:
            return None

        # Tentar extrair do formato 'TITULO: ...'
        match = re.search(r"TITULO:\s*(.*?)(?:\n|$)", new_name, re.IGNORECASE)
        if match:
            new_name = match.group(1).strip()
        else:
            # Fallback: pega a última linha não vazia, ou a primeira linha, tentando adivinhar
            lines = [line.strip() for line in new_name.split("\n") if line.strip() and not line.startswith("#")]
            if lines:
                # O modelo pode colocar o titulo no fim (como vimos) ou no começo
                new_name = lines[-1] if len(lines[-1]) < 40 else lines[0]
            
        # Limpar o nome (remover aspas, pontuação, asteriscos, etc.)
        new_name = new_name.replace("*", "").replace("`", "")
        new_name = new_name.strip('"\'.,!?;: ')
        
        # Limitar a 30 chars
        if len(new_name) > 30:
            new_name = new_name[:30].strip()

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
