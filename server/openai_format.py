"""
Модуль форматирования сообщений и ответов в стиле OpenAI API.
Включает:
- Склейку истории сообщений (messages) в единый промпт для веб-интерфейса;
- Формирование SSE-чанков для stream: true;
- Формирование JSON-ответа для stream: false;
- Форматирование ошибок по спецификации OpenAI.
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple


class UnsupportedModalityError(ValueError):
    """Исключение при передаче неподдерживаемых модальностей (например, изображений)."""
    pass


def extract_text_from_content(content: Any) -> str:
    """
    Извлекает чистый текст из поля content сообщения OpenAI.
    Проверяет наличие изображений и выбрасывает UnsupportedModalityError.
    """
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict):
                part_type = part.get("type", "")
                if part_type == "text":
                    text_parts.append(str(part.get("text", "")))
                elif part_type in ("image_url", "image", "input_audio", "file"):
                    raise UnsupportedModalityError(
                        f"Модальность '{part_type}' пока не поддерживается в arena-bridge. Используйте только текст."
                    )
            elif isinstance(part, str):
                text_parts.append(part)
        return "\n".join(text_parts)
    return str(content or "")


def format_messages_to_prompt(messages: List[Dict[str, Any]], format_type: str = "role_blocks") -> str:
    """
    Склеивает список сообщений (system, user, assistant) в один текстовый промпт.
    
    Поддерживаемые форматы:
    - "role_blocks":
        [System]
        Ты полезный ассистент.
        
        [User]
        Привет!
        
    - "plain":
        System: Ты полезный ассистент.
        User: Привет!
        
    - "chatml":
        <|im_start|>system
        Ты полезный ассистент.<|im_end|>
        <|im_start|>user
        Привет!<|im_end|>
    """
    if not messages:
        return ""

    # Если передано одно сообщение пользователя, отдаём его напрямую без лишних префиксов
    if len(messages) == 1 and messages[0].get("role") == "user":
        return extract_text_from_content(messages[0].get("content", ""))

    blocks = []
    fmt = (format_type or "role_blocks").lower()

    for msg in messages:
        role = str(msg.get("role", "user")).lower()
        content = extract_text_from_content(msg.get("content", "")).strip()
        if not content:
            continue

        if fmt == "chatml":
            blocks.append(f"<|im_start|>{role}\n{content}<|im_end|>")
        elif fmt == "plain":
            role_label = role.capitalize()
            blocks.append(f"{role_label}: {content}")
        else:  # role_blocks (по умолчанию)
            role_name = role.capitalize()
            blocks.append(f"[{role_name}]\n{content}")

    if fmt == "chatml":
        # Добавляем приглашение к ответу ассистента
        return "\n".join(blocks) + "\n<|im_start|>assistant\n"
    elif fmt == "plain":
        return "\n\n".join(blocks) + "\n\nAssistant: "
    else:
        return "\n\n".join(blocks)


def format_sse_chunk(
    chunk_id: str,
    model: str,
    content: Optional[str] = None,
    reasoning_content: Optional[str] = None,
    role: Optional[str] = None,
    finish_reason: Optional[str] = None,
    created_at: Optional[int] = None,
) -> str:
    """
    Форматирует один SSE-чанк OpenAI формата chat.completion.chunk.
    Поддерживает как обычный текст, так и reasoning_content (для reasoning-моделей).
    """
    delta: Dict[str, Any] = {}
    if role:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    if reasoning_content is not None:
        delta["reasoning_content"] = reasoning_content

    chunk_data = {
        "id": f"chatcmpl-{chunk_id}",
        "object": "chat.completion.chunk",
        "created": created_at or int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }

    return f"data: {json.dumps(chunk_data, ensure_ascii=False)}\n\n"


def format_sse_done() -> str:
    """Возвращает финальный сигнал завершения SSE-стрима."""
    return "data: [DONE]\n\n"


def format_sse_error(message: str, error_type: str = "bridge_error", code: int = 500) -> str:
    """Форматирует чанк с ошибкой для SSE."""
    payload = {
        "error": {
            "message": message,
            "type": error_type,
            "code": code,
        }
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def format_completion_response(
    request_id: str,
    model: str,
    content: str,
    reasoning_content: Optional[str] = None,
    finish_reason: str = "stop",
    created_at: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Форматирует полный ответ для stream: false в формате OpenAI chat.completion.
    """
    message_dict: Dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if reasoning_content:
        message_dict["reasoning_content"] = reasoning_content

    # Грубая оценка токенов (1 токен ~ 4 символа)
    prompt_tokens = max(1, len(content) // 4)
    completion_tokens = max(1, len(content) // 4)

    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "created": created_at or int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message_dict,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def make_openai_error_response(
    message: str,
    error_type: str = "invalid_request_error",
    code: Optional[str] = None,
    param: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Создает тело ошибки в стандартном формате OpenAI.
    """
    err = {
        "message": message,
        "type": error_type,
        "param": param,
        "code": code,
    }
    return {"error": err}
