"""
Интеграционные тесты для FastAPI API сервера arena-bridge.
"""

import asyncio
import json
import pytest
from httpx import AsyncClient, ASGITransport

from server.main import app, bridge_manager, config, model_catalog


@pytest.fixture(autouse=True)
def setup_test_config():
    # Настраиваем тестовый API ключ
    config._data["api_key"] = "sk-test-key-12345"
    yield


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "userscript_connected" in data
        assert "queue_length" in data


@pytest.mark.asyncio
async def test_status_page():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "arena-bridge" in resp.text
        assert "Userscript" in resp.text


@pytest.mark.asyncio
async def test_userscript_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/userscript.user.js")
        assert resp.status_code == 200
        assert "// ==UserScript==" in resp.text
        assert "arena-bridge" in resp.text


@pytest.mark.asyncio
async def test_auth_missing_header():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/models")
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_auth_invalid_key():
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer wrong-key"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        resp = await client.get("/v1/models")
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_list_models_with_valid_key():
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer sk-test-key-12345"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        resp = await client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert len(data["data"]) > 0
        model_ids = [m["id"] for m in data["data"]]
        assert "gemini-3.1-pro-preview" in model_ids


@pytest.mark.asyncio
async def test_chat_completions_503_when_no_userscript():
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer sk-test-key-12345"}
    # Убеждаемся, что userscript не подключен
    bridge_manager._active_ws = None

    payload = {
        "model": "gemini-3.1-pro-preview",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        resp = await client.post("/v1/chat/completions", json=payload)
        assert resp.status_code == 503
        data = resp.json()
        assert "Откройте вкладку arena.ai" in data["error"]["message"]


@pytest.mark.asyncio
async def test_chat_completions_404_unknown_model():
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer sk-test-key-12345"}

    class MockWebSocket:
        async def send_text(self, text):
            pass

    bridge_manager._active_ws = MockWebSocket()

    payload = {
        "model": "non-existent-super-model-9000",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    try:
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
            resp = await client.post("/v1/chat/completions", json=payload)
            assert resp.status_code == 404
            data = resp.json()
            assert "не найдена" in data["error"]["message"]
    finally:
        bridge_manager._active_ws = None


@pytest.mark.asyncio
async def test_chat_completions_mock_stream_and_non_stream():
    """Тестирует полный цикл stream: true и stream: false с имитацией ответов userscript."""
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer sk-test-key-12345"}

    class MockWSClient:
        async def send_text(self, text):
            msg = json.loads(text)
            action = msg.get("action")
            if action == "chat":
                req_id = msg.get("id")
                # Имитируем стриминг ответов от Userscript в фоне
                asyncio.create_task(self._simulate_response(req_id))

        async def _simulate_response(self, req_id):
            await asyncio.sleep(0.05)
            # Отправка чанков
            await bridge_manager.handle_incoming_message(
                json.dumps({"type": "chunk", "id": req_id, "content": "Привет, "})
            )
            await bridge_manager.handle_incoming_message(
                json.dumps({"type": "chunk", "id": req_id, "content": "мир!"})
            )
            # Завершение
            await bridge_manager.handle_incoming_message(
                json.dumps({"type": "done", "id": req_id, "sessionId": "session-test-uuid-7"})
            )
            # Подтверждение удаления
            await bridge_manager.handle_incoming_message(
                json.dumps({"type": "deleted", "id": req_id, "sessionId": "session-test-uuid-7", "success": True})
            )

    bridge_manager._active_ws = MockWSClient()

    try:
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
            # 1. Non-stream тест
            resp_non_stream = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3.1-pro-preview",
                    "messages": [{"role": "user", "content": "Привет"}],
                    "stream": False,
                },
            )
            assert resp_non_stream.status_code == 200
            data = resp_non_stream.json()
            assert data["object"] == "chat.completion"
            assert data["choices"][0]["message"]["content"] == "Привет, мир!"

            # 2. Stream тест
            resp_stream = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3.1-pro-preview",
                    "messages": [{"role": "user", "content": "Привет"}],
                    "stream": True,
                },
            )
            assert resp_stream.status_code == 200
            stream_text = resp_stream.text
            assert "data: [DONE]" in stream_text
            assert "Привет, " in stream_text
            assert "мир!" in stream_text
    finally:
        bridge_manager._active_ws = None
