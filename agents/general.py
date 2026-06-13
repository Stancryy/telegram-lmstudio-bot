"""agents/general.py — Agente para conversas gerais."""

from agents.base import Agent

general_agent = Agent(
    name="General",
    emoji="💬",
    temperature=0.7,
    description="Conversas gerais, perguntas simples e interações do dia-a-dia.",
    system_prompt=(
        "Você é um assistente virtual prestativo, inteligente e educado. "
        "Responda de forma clara e concisa. Seja amigável e natural na conversa. "
        "Se não souber algo, diga honestamente."
    ),
)
