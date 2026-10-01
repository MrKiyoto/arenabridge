"""
Модуль WebSocket-моста (bridge) между Python-сервером и Userscript в браузере.
Управляет:
- WebSocket-соединением с Userscript (с heartbeat / ping-pong);
- Передачей заданий на генерацию ответа;
- Приемом потоковых чанков (стриминг);
- Гарантированным удалением созданных чатов;
- Очередью недоудалённых чатов (failed_deletions.json).
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("arena_bridge.bridge")


class BridgeManager:
    def __init__(self, failed_deletions_file: str = "failed_deletions.json"):
        self._active_ws: Optional[WebSocket] = None
        self._pending_requests: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._failed_deletions_file = Path(failed_deletions_file)
        self._known_created_sessions: Set[str] = set()
        self._last_heartbeat: float = 0.0
        self._heartbeat_task: Optional[asyncio.Task] = None

        # Загрузка недоудалённых сессий из файла при старте
        self._failed_session_ids = self._load_failed_deletions()

    @property
    def is_connected(self) -> bool:
        """Подключен ли сейчас userscript по WebSocket."""
        return self._active_ws is not None

    def _load_failed_deletions(self) -> Set[str]:
        """Загружает список ID сессий, которые не удалось удалить ранее."""
        if not self._failed_deletions_file.exists():
            return set()
        try:
            with open(self._failed_deletions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(str(x) for x in data if x)
        except Exception as e:
            logger.error(f"Ошибка при чтении {self._failed_deletions_file}: {e}")
        return set()

    def _save_failed_deletions(self) -> None:
        """Сохраняет список недоудалённых ID сессий на диск."""
        try:
            with open(self._failed_deletions_file, "w", encoding="utf-8") as f:
                json.dump(list(self._failed_session_ids), f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка при сохранении {self._failed_deletions_file}: {e}")

    def add_failed_deletion(self, session_id: str) -> None:
        """Добавляет session_id в очередь повторного удаления."""
        if not session_id or session_id not in self._known_created_sessions:
            # Безопасность: никогда не удаляем чаты, созданные не нами
            return
        self._failed_session_ids.add(session_id)
        self._save_failed_deletions()
        logger.warning(f"Чат {session_id} добавлен в очередь повторного удаления.")

    def remove_failed_deletion(self, session_id: str) -> None:
        """Удаляет session_id из очереди повторного удаления."""
        if session_id in self._failed_session_ids:
            self._failed_session_ids.remove(session_id)
            self._save_failed_deletions()
            logger.info(f"Чат {session_id} успешно удалён из очереди повторных удалений.")

    async def register_connection(self, websocket: WebSocket) -> None:
        """Регистрирует новое WebSocket-соединение от userscript."""
        await websocket.accept()
        async with self._lock:
            if self._active_ws is not None:
                try:
                    await self._active_ws.close(code=1000, reason="Новое подключение userscript")
                except Exception:
                    pass
            self._active_ws = websocket
            self._last_heartbeat = time.time()
            logger.info("Userscript успешно подключен к WebSocket-мосту.")

        # Если есть недоудалённые чаты, отправляем запрос на их удаление
        if self._failed_session_ids:
            asyncio.create_task(self._trigger_pending_deletions())

        # Запуск фонового heartbeat
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def unregister_connection(self, websocket: WebSocket) -> None:
        """Отключает WebSocket."""
        async with self._lock:
            if self._active_ws == websocket:
                self._active_ws = None
                logger.warning("Userscript отключился от WebSocket-моста.")

                # Уведомляем все зависшие запросы об обрыве
                for req_id, req_data in list(self._pending_requests.items()):
                    queue: asyncio.Queue = req_data.get("queue")
                    session_id = req_data.get("session_id")
                    if session_id:
                        self.add_failed_deletion(session_id)
                    if queue:
                        await queue.put({"type": "error", "error": "Userscript отключился во время выполнения запроса."})

    async def _heartbeat_loop(self) -> None:
        """Фоновый цикл отправки ping каждые 12 секунд для предотвращения засыпания в Termux/браузере."""
        try:
            while self.is_connected:
                await asyncio.sleep(12)
                ws = self._active_ws
                if ws:
                    try:
                        await ws.send_text(json.dumps({"action": "ping"}))
                    except Exception:
                        break
        except asyncio.CancelledError:
            pass

    async def handle_incoming_message(self, text: str) -> None:
        """Обрабатывает входящее сообщение от userscript."""
        try:
            data = json.loads(text)
        except Exception:
            return

        msg_type = data.get("type")

        if msg_type == "pong":
            self._last_heartbeat = time.time()
            return

        req_id = data.get("id")
        if not req_id or req_id not in self._pending_requests:
            # Обработка системных сообщений вне конкретного запроса
            if msg_type == "pending_deleted":
                deleted_ids = data.get("sessionIds", [])
                for sid in deleted_ids:
                    self.remove_failed_deletion(sid)
            return

        req_info = self._pending_requests[req_id]
        queue: asyncio.Queue = req_info["queue"]

        # Если userscript сообщил ID созданной сессии, сохраняем для безопасного удаления
        session_id = data.get("sessionId")
        if session_id:
            req_info["session_id"] = session_id
            self._known_created_sessions.add(session_id)

        if msg_type == "deleted":
            if session_id:
                self.remove_failed_deletion(session_id)

        await queue.put(data)

    async def _trigger_pending_deletions(self) -> None:
        """Отправляет userscript'у задание на удаление накопленных незакрытых сессий."""
        if not self.is_connected or not self._failed_session_ids:
            return
        try:
            payload = {
                "action": "delete_pending",
                "sessionIds": list(self._failed_session_ids),
            }
            if self._active_ws:
                await self._active_ws.send_text(json.dumps(payload))
        except Exception as e:
            logger.warning(f"Не удалось отправить запрос на повторное удаление чатов: {e}")

    async def send_chat_task(
        self,
        request_id: str,
        model_id: str,
        prompt: str,
        delete_chat: bool = True,
    ) -> None:
        """Отправляет userscript'у задание создать чат и отправить сообщение."""
        if not self.is_connected or not self._active_ws:
            raise ConnectionError("Userscript не подключен к серверу.")

        payload = {
            "action": "chat",
            "id": request_id,
            "modelId": model_id,
            "prompt": prompt,
            "deleteChat": delete_chat,
        }
        await self._active_ws.send_text(json.dumps(payload))

    async def stream_chat(
        self,
        request_id: str,
        model_id: str,
        prompt: str,
        delete_chat: bool = True,
        timeout: float = 180.0,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Асинхронный генератор, отправляющий задание userscript'у и отдающий чанки ответа.
        Гарантирует удаление чата при любом исходе (успех, ошибка, разрыв клиента).
        """
        if not self.is_connected:
            raise ConnectionError("Откройте вкладку arena.ai с включённым userscript.")

        queue = asyncio.Queue()
        self._pending_requests[request_id] = {
            "queue": queue,
            "session_id": None,
            "created_at": time.time(),
        }

        try:
            # Отправка задачи в userscript
            await self.send_chat_task(
                request_id=request_id,
                model_id=model_id,
                prompt=prompt,
                delete_chat=delete_chat,
            )

            start_time = time.monotonic()

            while True:
                remaining_time = timeout - (time.monotonic() - start_time)
                if remaining_time <= 0:
                    raise asyncio.TimeoutError("Превышен общий таймаут ожидания ответа от arena.ai.")

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=min(remaining_time, 30.0))
                except asyncio.TimeoutError:
                    # Проверяем, жива ли еще связь
                    if not self.is_connected:
                        raise ConnectionError("Userscript отключился во время ожидания чанка.")
                    continue

                event_type = event.get("type")

                if event_type == "chunk":
                    yield {
                        "content": event.get("content"),
                        "reasoning_content": event.get("reasoning"),
                        "finish_reason": None,
                    }
                elif event_type == "done":
                    # Завершение генерации
                    session_id = event.get("sessionId")
                    yield {
                        "content": None,
                        "reasoning_content": None,
                        "finish_reason": "stop",
                        "sessionId": session_id,
                    }
                    break
                elif event_type == "error":
                    err_msg = event.get("error", "Неизвестная ошибка на arena.ai")
                    raise RuntimeError(err_msg)

        finally:
            # Очистка и гарантия удаления чата
            req_info = self._pending_requests.pop(request_id, {})
            session_id = req_info.get("session_id")

            if delete_chat and session_id:
                # Если userscript всё ещё подключен, пробуем отправить команду на удаление
                if self.is_connected and self._active_ws:
                    try:
                        await self._active_ws.send_text(
                            json.dumps({
                                "action": "delete_chat",
                                "id": request_id,
                                "sessionId": session_id,
                            })
                        )
                    except Exception as e:
                        logger.error(f"Не удалось отправить команду удаления для {session_id}: {e}")
                        self.add_failed_deletion(session_id)
                else:
                    self.add_failed_deletion(session_id)
