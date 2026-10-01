"""
Тесты для модуля SSE и ответов OpenAI (openai_format.py).
"""

import json
from server.openai_format import (
    format_completion_response,
    format_sse_chunk,
    format_sse_done,
    format_sse_error,
    make_openai_error_response,
)


def test_format_sse_chunk_content():
    chunk = format_sse_chunk(chunk_id="test-123", model="test-model", content="Hello")
    assert chunk.startswith("data: ")
    assert chunk.endswith("\n\n")

    json_str = chunk[5:].strip()
    data = json.loads(json_str)
    assert data["id"] == "chatcmpl-test-123"
    assert data["object"] == "chat.completion.chunk"
    assert data["model"] == "test-model"
    choice = data["choices"][0]
    assert choice["delta"]["content"] == "Hello"
    assert choice["finish_reason"] is None


def test_format_sse_chunk_reasoning():
    chunk = format_sse_chunk(chunk_id="test-123", model="test-model", reasoning_content="Thinking step...")
    json_str = chunk[5:].strip()
    data = json.loads(json_str)
    assert data["choices"][0]["delta"]["reasoning_content"] == "Thinking step..."


def test_format_sse_done():
    done = format_sse_done()
    assert done == "data: [DONE]\n\n"


def test_format_sse_error():
    err = format_sse_error("Something went wrong", code=503)
    json_str = err[5:].strip()
    data = json.loads(json_str)
    assert data["error"]["message"] == "Something went wrong"
    assert data["error"]["code"] == 503


def test_format_completion_response_non_stream():
    res = format_completion_response(
        request_id="req-456",
        model="gpt-5.5-instant",
        content="Complete answer",
        reasoning_content="Summary of thoughts",
    )
    assert res["id"] == "chatcmpl-req-456"
    assert res["object"] == "chat.completion"
    assert res["model"] == "gpt-5.5-instant"
    msg = res["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] == "Complete answer"
    assert msg["reasoning_content"] == "Summary of thoughts"
    assert res["choices"][0]["finish_reason"] == "stop"


def test_make_openai_error_response():
    err = make_openai_error_response("Not found", error_type="invalid_request_error", code="404")
    assert "error" in err
    assert err["error"]["message"] == "Not found"
    assert err["error"]["type"] == "invalid_request_error"
    assert err["error"]["code"] == "404"
