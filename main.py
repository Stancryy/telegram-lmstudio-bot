"""
main.py — Entry point do Bot Telegram + LM Studio.

Inicializa o bot, registra handlers e inicia o polling.
Toda a lógica foi modularizada em:
  - bot/       → config, formatação, handlers, streaming
  - agents/    → sistema multi-agente com roteador
  - persistence/ → histórico e memória de longo prazo
"""

import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

from bot.config import (
    TELEGRAM_BOT_TOKEN,
    MEMPALACE_ENABLED,
    AGENTS_ENABLED,
    client,
    LM_STUDIO_URL,
)
from bot.handlers import (
    start,
    help_command,
    new_chat,
    list_chats,
    switch_chat,
    rename_chat,
    delete_chat,
    clear,
    retry_command,
    export_chat,
    status_command,
    handle_message,
    # Multi-agente
    agents_command,
    agent_command,
    # MemPalace
    remember_command,
    memory_status_command,
    forget_command,
)
from persistence.history import load_history, save_history_now
from persistence import mempalace_adapter as mem
from agents import init_agents

# Configura logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application) -> None:
    """Executado após a inicialização do bot."""
    # Carregar histórico
    await load_history()

    # Health check do LM Studio
    try:
        models = await client.models.list()
        model_names = [m.id for m in models.data]
        logger.info("LM Studio conectado. Modelos: %s", model_names)
    except Exception as e:
        logger.warning("LM Studio nao acessivel em %s: %s", LM_STUDIO_URL, e)
        logger.warning("O bot sera iniciado mesmo assim.")

    # Inicializar agentes
    if AGENTS_ENABLED:
        agents = init_agents()
        logger.info("Sistema multi-agente ativo com %d agentes.", len(agents))
    else:
        logger.info("Sistema multi-agente desabilitado.")

    # Inicializar MemPalace
    if MEMPALACE_ENABLED:
        success = await mem.init_palace()
        if success:
            logger.info("MemPalace habilitado - memoria de longo prazo ativa!")
        else:
            logger.warning("MemPalace habilitado mas falhou na inicializacao.")
    else:
        logger.info("MemPalace desabilitado.")


async def on_shutdown(application) -> None:
    """Garantir que o histórico pendente seja salvo antes de encerrar."""
    await save_history_now()
    logger.info("Historico salvo. Bot encerrado com sucesso.")


if __name__ == '__main__':
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "seu_token_do_telegram_aqui":
        logger.error("Token do Telegram nao encontrado! Configure o arquivo .env.")
        exit(1)

    # Timeouts maiores para evitar TimedOut em respostas longas
    request = HTTPXRequest(
        read_timeout=30.0,
        write_timeout=30.0,
        connect_timeout=15.0,
    )

    application = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
        .post_init(post_init)
        .post_shutdown(on_shutdown)
        .build()
    )

    # Comandos básicos
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('new', new_chat))
    application.add_handler(CommandHandler('chats', list_chats))
    application.add_handler(CommandHandler('switch', switch_chat))
    application.add_handler(CommandHandler('rename', rename_chat))
    application.add_handler(CommandHandler('delete', delete_chat))
    application.add_handler(CommandHandler('clear', clear))
    application.add_handler(CommandHandler('retry', retry_command))
    application.add_handler(CommandHandler('export', export_chat))
    application.add_handler(CommandHandler('status', status_command))

    # Comandos multi-agente
    application.add_handler(CommandHandler('agents', agents_command))
    application.add_handler(CommandHandler('agent', agent_command))

    # Comandos MemPalace
    application.add_handler(CommandHandler('remember', remember_command))
    application.add_handler(CommandHandler('memory', memory_status_command))
    application.add_handler(CommandHandler('forget', forget_command))

    # Handler de mensagens (texto + imagem)
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO) & (~filters.COMMAND),
            handle_message,
        )
    )

    logger.info("Bot iniciado! Pressione Ctrl+C para parar.")
    application.run_polling()
