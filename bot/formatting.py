"""
bot/formatting.py — Formatação de texto para o Telegram.

Converte Markdown básico para HTML do Telegram e divide textos longos
de forma segura, sem quebrar tags HTML no meio.
"""

import re
import html as html_lib


def format_to_html(text: str) -> str:
    """Converte Markdown básico para HTML do Telegram de forma segura."""
    # Primeiro escapa caracteres HTML especiais
    text = html_lib.escape(text)

    # Code blocks: ```lang\ncode\n``` → <pre><code>code</code></pre>
    def replace_code_block(match: re.Match) -> str:
        code = match.group(2)
        return f"<pre><code>{code}</code></pre>"
    text = re.sub(r'```(\w*)\n(.*?)```', replace_code_block, text, flags=re.DOTALL)

    # Inline code: `code` → <code>code</code>
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)

    # Headers: # Título → <b>Título</b>
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # Bold + Italic: ***text*** → <b><i>text</i></b>
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', text)

    # Bold: **text** → <b>text</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

    # Italic: *text* → <i>text</i> (mas não dentro de tags já processadas)
    text = re.sub(r'(?<!\w)\*([^\*]+?)\*(?!\w)', r'<i>\1</i>', text)

    # Strikethrough: ~~text~~ → <s>text</s>
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # Links: [texto](url) → <a href="url">texto</a>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # Unordered lists: - item ou * item → • item
    text = re.sub(r'^[\-\*]\s+', '• ', text, flags=re.MULTILINE)

    # Ordered lists: 1. item → 1. item (mantém numeração, só limpa espaço)
    text = re.sub(r'^(\d+)\.\s+', r'\1. ', text, flags=re.MULTILINE)

    # Blockquotes: > texto → ❝ texto
    text = re.sub(r'^&gt;\s?(.+)$', r'❝ <i>\1</i>', text, flags=re.MULTILINE)

    return text


def split_text_safely(raw_text: str, max_length: int) -> list[str]:
    """Divide texto bruto em partes, quebrando em limites naturais (newlines, espaços).

    Cada parte é formatada para HTML separadamente, evitando tags cortadas.
    """
    parts = []
    # Reservar espaço para overhead de formatação HTML (~20%)
    safe_length = int(max_length * 0.8)

    while raw_text:
        if len(raw_text) <= safe_length:
            parts.append(raw_text)
            break

        # Tentar quebrar em uma nova linha
        split_pos = raw_text.rfind('\n', 0, safe_length)
        if split_pos == -1:
            # Tentar quebrar em um espaço
            split_pos = raw_text.rfind(' ', 0, safe_length)
        if split_pos == -1:
            # Forçar quebra
            split_pos = safe_length

        parts.append(raw_text[:split_pos])
        raw_text = raw_text[split_pos:].lstrip('\n')

    return parts
