"""Chat formatting shared by training, the CLI and the server."""
from __future__ import annotations

from .tokenizer import ASSISTANT, END, EOT, SYSTEM, USER

ROLE_TOKENS = {"system": SYSTEM, "user": USER, "assistant": ASSISTANT}
STOP_TOKENS = (END, EOT, USER, SYSTEM)


def _content(message: dict) -> str:
    content = message.get("content") or ""
    if isinstance(content, list):  # OpenAI "content parts"
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def render(messages: list[dict], add_generation_prompt: bool = False) -> str:
    out = []
    for m in messages:
        role = ROLE_TOKENS.get(m.get("role", "user"), USER)
        out.append(f"{role}{_content(m)}{END}")
    if add_generation_prompt:
        out.append(ASSISTANT)
    return "".join(out)


def render_document(record: dict) -> str:
    """Turn one JSONL training record into text: ``{"messages": [...]}`` or ``{"text": "..."}``."""
    if "messages" in record:
        return render(record["messages"]) + EOT
    return str(record.get("text", "")) + EOT
