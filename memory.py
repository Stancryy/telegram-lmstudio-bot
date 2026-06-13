"""
memory.py — Módulo de memória semântica de longo prazo usando MemPalace.

Encapsula todas as interações com o MemPalace, fornecendo uma interface
async-friendly para o bot Telegram. Cada usuário tem seu próprio "wing"
no palace, e cada sessão de chat é um "room".

Todas as operações do MemPalace são síncronas, então são executadas
via asyncio.to_thread() para não bloquear o event loop.
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Estado global do módulo
_palace_initialized = False
_palace_path: Optional[str] = None
_mempalace_available = False

# Tentar importar o MemPalace
try:
    from mempalace.searcher import search as _mp_search
    from mempalace.config import MempalaceConfig
    _mempalace_available = True
except ImportError:
    logger.warning(
        "⚠️ MemPalace não está instalado. "
        "Instale com: pip install mempalace"
    )
    _mempalace_available = False


def is_available() -> bool:
    """Verifica se o MemPalace está disponível e habilitado."""
    return _mempalace_available and _palace_initialized


def _get_palace_path() -> str:
    """Retorna o caminho do palace, usando o padrão ~/.mempalace/palace."""
    global _palace_path
    if _palace_path:
        return _palace_path

    # Usar o caminho padrão do MemPalace
    try:
        config = MempalaceConfig()
        _palace_path = str(config.palace_path)
    except Exception:
        _palace_path = os.path.join(Path.home(), ".mempalace", "palace")

    return _palace_path


def _ensure_palace_dir() -> str:
    """Garante que o diretório do palace existe."""
    palace_path = _get_palace_path()
    os.makedirs(palace_path, exist_ok=True)
    return palace_path


def _init_palace_sync() -> bool:
    """Inicializa o palace (síncrono). Retorna True se bem-sucedido."""
    try:
        palace_path = _ensure_palace_dir()
        logger.info("🏛️ MemPalace palace em: %s", palace_path)
        return True
    except Exception as e:
        logger.error("Erro ao inicializar MemPalace: %s", e)
        return False


async def init_palace() -> bool:
    """Inicializa o MemPalace palace (async wrapper)."""
    global _palace_initialized
    if not _mempalace_available:
        logger.info("MemPalace não instalado — memória de longo prazo desabilitada.")
        return False

    result = await asyncio.to_thread(_init_palace_sync)
    _palace_initialized = result
    if result:
        logger.info("🏛️ MemPalace inicializado com sucesso!")
    return result


def _search_memories_sync(
    query: str,
    wing: Optional[str] = None,
    n_results: int = 3,
) -> list[dict]:
    """Busca memórias no palace (síncrono).

    Retorna lista de dicts com:
        - text: conteúdo do drawer
        - wing: wing do resultado
        - room: room do resultado
        - similarity: score de similaridade
    """
    if not is_available():
        return []

    palace_path = _get_palace_path()

    try:
        # Importar o que precisamos para busca programática
        from mempalace.palace import get_collection, _open_collection_or_explain
        from mempalace.searcher import (
            build_where_filter,
            _first_or_empty,
            _hybrid_rank,
        )

        col = _open_collection_or_explain(palace_path, opener=get_collection)
        if col is None:
            return []

        where = build_where_filter(wing=wing)

        kwargs = {
            "query_texts": [query],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = col.query(**kwargs)

        docs = _first_or_empty(results, "documents")
        metas = _first_or_empty(results, "metadatas")
        dists = _first_or_empty(results, "distances")

        if not docs:
            return []

        # Aplicar ranking híbrido (vector + BM25)
        hits = [
            {"text": doc or "", "distance": float(dist), "metadata": meta or {}}
            for doc, meta, dist in zip(docs, metas, dists)
        ]
        hits = _hybrid_rank(hits, query)

        # Formatar resultados
        formatted = []
        for hit in hits:
            meta = hit.get("metadata", {})
            vec_sim = round(max(0.0, 1.0 - hit.get("distance", 1.0)), 3)
            formatted.append({
                "text": hit["text"],
                "wing": meta.get("wing", "desconhecido"),
                "room": meta.get("room", "geral"),
                "source": Path(meta.get("source_file", "?")).name,
                "similarity": vec_sim,
            })

        return formatted

    except Exception as e:
        logger.error("Erro na busca MemPalace: %s", e)
        return []


async def search_memories(
    query: str,
    wing: Optional[str] = None,
    n_results: int = 3,
) -> list[dict]:
    """Busca memórias relevantes no palace (async wrapper)."""
    if not is_available():
        return []
    return await asyncio.to_thread(_search_memories_sync, query, wing, n_results)


def _mine_text_sync(
    text: str,
    wing: str = "telegram_bot",
    room: str = "conversas",
    source_label: str = "telegram_chat",
) -> bool:
    """Indexa texto no palace como drawers (síncrono).

    Usa a API interna do MemPalace para adicionar documentos diretamente
    à collection do ChromaDB.
    """
    if not is_available() or not text.strip():
        return False

    palace_path = _get_palace_path()

    try:
        from mempalace.palace import get_collection, _open_collection_or_explain

        col = _open_collection_or_explain(palace_path, opener=get_collection)
        if col is None:
            # Tentar criar a collection
            col = get_collection(palace_path, create=True)
            if col is None:
                logger.warning("Não foi possível criar a collection do MemPalace.")
                return False

        # Dividir o texto em chunks menores se necessário
        chunks = _split_into_chunks(text, max_chars=1000)

        timestamp = str(int(time.time()))
        ids = []
        documents = []
        metadatas = []

        for i, chunk in enumerate(chunks):
            chunk_text = chunk.strip()
            if not chunk_text:
                continue

            doc_id = f"{wing}_{room}_{timestamp}_{i}"
            ids.append(doc_id)
            documents.append(chunk_text)
            metadatas.append({
                "wing": wing,
                "room": room,
                "source_file": source_label,
                "chunk_index": i,
                "timestamp": timestamp,
            })

        if not ids:
            return False

        col.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )

        logger.info(
            "🏛️ MemPalace: %d drawer(s) indexado(s) em %s/%s",
            len(ids), wing, room,
        )
        return True

    except Exception as e:
        logger.error("Erro ao indexar no MemPalace: %s", e)
        return False


def _split_into_chunks(text: str, max_chars: int = 1000) -> list[str]:
    """Divide texto em chunks, quebrando em limites naturais."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = ""

    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_chars and current:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line

    if current:
        chunks.append(current)

    return chunks


async def mine_conversation(
    user_id: int,
    session_id: str,
    messages: list[dict],
    wing: str = "telegram_bot",
) -> bool:
    """Indexa uma conversa no palace (async wrapper).

    Formata as mensagens como texto legível e salva como drawers.
    Pula mensagens de system e mensagens já mineradas.
    """
    if not is_available():
        return False

    # Filtrar apenas mensagens user/assistant relevantes
    relevant = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            continue
        if not isinstance(content, str):
            content = "[Conteúdo multimídia]"
        if not content.strip():
            continue

        prefix = "Usuário" if role == "user" else "Assistente"
        relevant.append(f"{prefix}: {content}")

    if not relevant:
        return False

    # Pegar apenas as últimas mensagens (par user+assistant mais recente)
    # para evitar re-indexar toda a conversa a cada mensagem
    recent = relevant[-2:] if len(relevant) >= 2 else relevant
    text = "\n\n".join(recent)

    room = f"sessao_{session_id}"
    source = f"user_{user_id}_session_{session_id}"

    return await asyncio.to_thread(
        _mine_text_sync, text, wing, room, source
    )


def _get_status_sync(wing: Optional[str] = None) -> dict:
    """Retorna status do palace (síncrono)."""
    if not is_available():
        return {"available": False, "reason": "MemPalace não inicializado"}

    palace_path = _get_palace_path()

    try:
        from mempalace.palace import get_collection, _open_collection_or_explain

        col = _open_collection_or_explain(palace_path, opener=get_collection)
        if col is None:
            return {
                "available": True,
                "total_drawers": 0,
                "palace_path": palace_path,
                "message": "Palace vazio — nenhuma memória armazenada ainda.",
            }

        count = col.count()

        # Tentar obter breakdown de wings
        wings_info = {}
        if count > 0:
            try:
                # Pegar uma amostra para listar wings
                sample = col.peek(limit=min(count, 100))
                if sample and sample.get("metadatas"):
                    for meta in sample["metadatas"]:
                        w = meta.get("wing", "desconhecido")
                        wings_info[w] = wings_info.get(w, 0) + 1
            except Exception:
                pass

        return {
            "available": True,
            "total_drawers": count,
            "palace_path": palace_path,
            "wings": wings_info,
        }

    except Exception as e:
        return {"available": True, "error": str(e)}


async def get_memory_status(wing: Optional[str] = None) -> dict:
    """Retorna status do palace (async wrapper)."""
    return await asyncio.to_thread(_get_status_sync, wing)


def _forget_sync(wing: str) -> dict:
    """Remove todas as memórias de um wing (síncrono)."""
    if not is_available():
        return {"success": False, "reason": "MemPalace não inicializado"}

    palace_path = _get_palace_path()

    try:
        from mempalace.palace import get_collection, _open_collection_or_explain

        col = _open_collection_or_explain(palace_path, opener=get_collection)
        if col is None:
            return {"success": False, "reason": "Palace não encontrado"}

        # Contar antes de deletar
        before_count = col.count()

        # Deletar por wing
        try:
            col.delete(where={"wing": wing})
        except Exception as e:
            return {"success": False, "reason": f"Erro ao deletar: {e}"}

        after_count = col.count()
        deleted = before_count - after_count

        return {
            "success": True,
            "deleted_drawers": deleted,
            "remaining_drawers": after_count,
        }

    except Exception as e:
        return {"success": False, "reason": str(e)}


async def forget_memories(wing: str) -> dict:
    """Remove todas as memórias de um wing (async wrapper)."""
    return await asyncio.to_thread(_forget_sync, wing)


def format_memories_for_context(memories: list[dict], max_tokens: int = 500) -> str:
    """Formata memórias encontradas como contexto para injetar no prompt.

    Retorna uma string formatada para ser adicionada como mensagem de sistema
    antes da chamada ao LLM. Limita o tamanho para não consumir muitos tokens.
    """
    if not memories:
        return ""

    lines = ["[Memórias relevantes de conversas anteriores:]"]

    char_count = len(lines[0])
    # Estimativa: 1 token ≈ 4 chars
    max_chars = max_tokens * 4

    for i, mem in enumerate(memories, 1):
        similarity = mem.get("similarity", 0)
        text = mem.get("text", "").strip()

        # Truncar textos muito longos
        if len(text) > 300:
            text = text[:300] + "..."

        entry = f"\n— Memória {i} (relevância: {similarity:.0%}): {text}"

        if char_count + len(entry) > max_chars:
            break

        lines.append(entry)
        char_count += len(entry)

    return "\n".join(lines)
