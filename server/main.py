"""
Основной модуль сервера arena-bridge.
Запускает FastAPI сервер с поддержкой:
- OpenAI-совместимого HTTP API (/v1/models, /v1/chat/completions);
- WebSocket-моста для Userscript (/ws);
- Раздачи скрипта (/userscript.user.js);
- Страницы статуса (/) и проверки здоровья (/health).
"""

import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .bridge import BridgeManager
from .config import Config, load_config
from .models import ModelCatalog
from .openai_format import (
    UnsupportedModalityError,
    format_completion_response,
    format_messages_to_prompt,
    format_sse_chunk,
    format_sse_done,
    format_sse_error,
    make_openai_error_response,
)
from .queue import RequestQueue

# Инициализация конфигурации
config: Config = load_config()

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, config.log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("arena_bridge.server")

# Инициализация компонентов
bridge_manager = BridgeManager(failed_deletions_file=config.failed_deletions_file)
model_catalog = ModelCatalog(models_override=config.models_override)
request_queue = RequestQueue(
    max_concurrency=config.max_concurrency,
    min_interval=config.min_request_interval,
    default_timeout=config.request_timeout,
)

app = FastAPI(
    title="arena-bridge",
    description="Local OpenAI-compatible API bridge to arena.ai for Android/Termux",
    version="1.0.0",
)

# Разрешаем CORS для всех клиентов (NextChat, Chatbox, OpenWebUI и др.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Модели валидации Pydantic ---

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Dict[str, Any]]
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    tools: Optional[List[Any]] = None
    tool_choice: Optional[Any] = None

    model_config = ConfigDict(extra="ignore")  # Игнорируем любые дополнительные параметры OpenAI


# --- Проверка авторизации ---

async def verify_api_key(authorization: Optional[str] = Header(None)) -> str:
    """Проверяет заголовок Authorization: Bearer <key>."""
    expected_key = config.api_key
    if not expected_key:
        return "anonymous"

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=make_openai_error_response(
                "API key required. Provide 'Authorization: Bearer <key>' header.",
                error_type="invalid_request_error",
                code="invalid_api_key",
            ),
        )

    parts = authorization.strip().split(" ", 1)
    token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else parts[0]

    if token != expected_key:
        logger.warning("Попытка доступа с неверным API-ключом.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=make_openai_error_response(
                "Invalid API key provided.",
                error_type="invalid_request_error",
                code="invalid_api_key",
            ),
        )

    return token


# --- Обработчик ошибок HTTP ---

@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """Возвращает ошибки всегда в стандартном формате OpenAI."""
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=make_openai_error_response(str(exc.detail), code=str(exc.status_code)),
    )


# --- Эндпоинты ---

@app.get("/", response_class=HTMLResponse)
async def status_page():
    """Простая страница статуса и инструкции."""
    ws_status = "🟢 Подключён" if bridge_manager.is_connected else "🔴 Ожидание подключения"
    models_count = len(await model_catalog.get_models())

    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>arena-bridge</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; line-height: 1.6; max-width: 650px; margin: 0 auto; }}
        h1 {{ color: #38bdf8; margin-bottom: 0.5rem; }}
        .card {{ background: #1e293b; border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; border: 1px solid #334155; }}
        .status-badge {{ font-weight: bold; font-size: 1.1rem; }}
        .btn {{ display: inline-block; background: #0284c7; color: white; padding: 0.6rem 1.2rem; text-decoration: none; border-radius: 6px; font-weight: 500; margin-top: 0.5rem; }}
        .btn:hover {{ background: #0369a1; }}
        code {{ background: #0f172a; padding: 0.2rem 0.4rem; border-radius: 4px; font-size: 0.9em; color: #f472b6; }}
    </style>
</head>
<body>
    <h1>🚀 arena-bridge</h1>
    <p>Локальный OpenAI API мост к <code>arena.ai</code> для Android/Termux</p>
    
    <div class="card">
        <h3>Состояние системы:</h3>
        <p>Userscript в браузере: <span class="status-badge">{ws_status}</span></p>
        <p>Запросов в очереди: <b>{request_queue.waiting_count}</b> (активных: <b>{request_queue.active_count}</b>)</p>
        <p>Доступных моделей: <b>{models_count}</b></p>
    </div>

    <div class="card">
        <h3>Быстрая настройка:</h3>
        <ol>
            <li>Установите расширение <b>Tampermonkey</b> или <b>Violentmonkey</b> в Firefox / Kiwi Browser.</li>
            <li>Нажмите кнопку ниже для установки скрипта моста:</li>
            <p><a href="/userscript.user.js" class="btn">📥 Установить Userscript в 1 клик</a></p>
            <li>Откройте вкладку <a href="https://arena.ai/text/direct" target="_blank" style="color: #38bdf8;">arena.ai/text/direct</a> и авторизуйтесь.</li>
            <li>В вашем клиенте укажите Base URL: <code>http://127.0.0.1:{config.port}/v1</code>.</li>
        </ol>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content)


@app.get("/health")
async def health_check():
    """Эндпоинт проверки здоровья сервиса."""
    return {
        "status": "ok",
        "userscript_connected": bridge_manager.is_connected,
        "queue_length": request_queue.waiting_count,
        "active_requests": request_queue.active_count,
        "version": "1.0.0",
    }


@app.get("/userscript.user.js")
async def get_userscript():
    """Отдает файл userscript с динамически подставленным хостом и портом сервера."""
    userscript_path = Path(__file__).resolve().parent.parent / "userscript" / "arena-bridge.user.js"
    if not userscript_path.exists():
        raise HTTPException(status_code=404, detail="Файл userscript не найден")

    with open(userscript_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Динамически подставляем актуальный порт сервера в userscript
    content = content.replace("ws://127.0.0.1:8000/ws", f"ws://127.0.0.1:{config.port}/ws")
    return PlainTextResponse(content=content, media_type="application/javascript")


@app.get("/v1/models")
async def list_models(user: str = Depends(verify_api_key)):
    """Возвращает список доступных моделей arena.ai в формате OpenAI."""
    models = await model_catalog.get_models()
    return model_catalog.format_openai_models_list(models)


@app.websocket("/ws")
async def websocket_bridge_endpoint(websocket: WebSocket):
    """WebSocket эндпоинт для связи с Userscript."""
    await bridge_manager.register_connection(websocket)
    try:
        while True:
            text = await websocket.receive_text()
            await bridge_manager.handle_incoming_message(text)
    except WebSocketDisconnect:
        await bridge_manager.unregister_connection(websocket)
    except Exception as e:
        logger.error(f"Ошибка в WebSocket соединении: {e}")
        await bridge_manager.unregister_connection(websocket)


@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    raw_request: Request,
    user: str = Depends(verify_api_key),
):
    """
    Обработчик OpenAI Chat Completions (stream: true и stream: false).
    Перенаправляет запрос в Userscript на arena.ai и возвращает ответ.
    """
    # 1. Проверяем наличие подключенного userscript
    if not bridge_manager.is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=make_openai_error_response(
                "Откройте вкладку arena.ai с включённым userscript",
                error_type="bridge_unavailable",
                code="service_unavailable",
            ),
        )

    # 2. Проверяем валидность модели
    model_uuid = await model_catalog.resolve_model_id(req.model)
    if not model_uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=make_openai_error_response(
                f"Модель '{req.model}' не найдена в каталоге arena.ai. Проверьте список через GET /v1/models.",
                error_type="invalid_request_error",
                code="model_not_found",
            ),
        )

    # 3. Склеиваем сообщения в единый промпт
    try:
        prompt_text = format_messages_to_prompt(req.messages, format_type=config.prompt_format)
    except UnsupportedModalityError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=make_openai_error_response(str(e), error_type="invalid_request_error", code="unsupported_modality"),
        )

    if not prompt_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=make_openai_error_response("Сообщение не может быть пустым.", error_type="invalid_request_error"),
        )

    request_id = str(uuid.uuid4())
    logger.info(f"Запрос {request_id[:8]} [модель={req.model}, stream={req.stream}] поставлен в очередь.")

    # Логируем игнорируемые параметры (для соответствия ТЗ)
    ignored_params = []
    if req.temperature is not None:
        ignored_params.append("temperature")
    if req.max_tokens is not None:
        ignored_params.append("max_tokens")
    if req.tools is not None:
        ignored_params.append("tools")
    if ignored_params:
        logger.debug(f"Параметры {ignored_params} приняты и проигнорированы веб-интерфейсом arena.ai.")

    # 4. Обработка стриминга (stream: true)
    if req.stream:
        async def event_generator():
            try:
                # Вход в очередь с таймаутом
                async with request_queue.acquire(timeout=config.request_timeout):
                    # Отправляем первый начальный чанк с ролью
                    yield format_sse_chunk(chunk_id=request_id, model=req.model, role="assistant")

                    async for chunk in bridge_manager.stream_chat(
                        request_id=request_id,
                        model_id=model_uuid,
                        prompt=prompt_text,
                        delete_chat=config.delete_chats,
                        timeout=config.request_timeout,
                    ):
                        # Проверка отключения клиента
                        if await raw_request.is_disconnected():
                            logger.info(f"Клиент отключился до завершения стрима {request_id[:8]}.")
                            break

                        content = chunk.get("content")
                        reasoning = chunk.get("reasoning_content")
                        finish_reason = chunk.get("finish_reason")

                        if content or reasoning or finish_reason:
                            yield format_sse_chunk(
                                chunk_id=request_id,
                                model=req.model,
                                content=content,
                                reasoning_content=reasoning,
                                finish_reason=finish_reason,
                            )

                    # Финальный сигнал DONE
                    yield format_sse_done()

            except asyncio.TimeoutError:
                yield format_sse_error("Превышено время ожидания ответа от arena.ai", code=504)
                yield format_sse_done()
            except Exception as e:
                logger.error(f"Ошибка в стриме {request_id[:8]}: {e}")
                yield format_sse_error(str(e), code=500)
                yield format_sse_done()

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 5. Обработка non-stream (stream: false)
    else:
        try:
            full_content_parts = []
            full_reasoning_parts = []

            async with request_queue.acquire(timeout=config.request_timeout):
                async for chunk in bridge_manager.stream_chat(
                    request_id=request_id,
                    model_id=model_uuid,
                    prompt=prompt_text,
                    delete_chat=config.delete_chats,
                    timeout=config.request_timeout,
                ):
                    c = chunk.get("content")
                    if c:
                        full_content_parts.append(c)
                    r = chunk.get("reasoning_content")
                    if r:
                        full_reasoning_parts.append(r)

            full_text = "".join(full_content_parts)
            full_reasoning = "".join(full_reasoning_parts) if full_reasoning_parts else None

            logger.info(f"Запрос {request_id[:8]} успешно выполнен (длина: {len(full_text)} симв).")

            return format_completion_response(
                request_id=request_id,
                model=req.model,
                content=full_text,
                reasoning_content=full_reasoning,
                finish_reason="stop",
            )

        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=make_openai_error_response("Превышено время ожидания ответа от arena.ai", code="timeout"),
            )
        except Exception as e:
            logger.error(f"Ошибка выполнения non-stream запроса {request_id[:8]}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=make_openai_error_response(str(e), error_type="server_error"),
            )
