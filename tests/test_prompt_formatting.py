"""
Тесты для модуля форматирования промптов (openai_format.py).
"""

import pytest
from server.openai_format import (
    UnsupportedModalityError,
    extract_text_from_content,
    format_messages_to_prompt,
)


def test_extract_text_simple_string():
    assert extract_text_from_content("Hello, world!") == "Hello, world!"


def test_extract_text_array_format():
    content = [
        {"type": "text", "text": "Part 1"},
        {"type": "text", "text": "Part 2"},
    ]
    assert extract_text_from_content(content) == "Part 1\nPart 2"


def test_reject_multimodal_images():
    content = [
        {"type": "text", "text": "Look at this:"},
        {"type": "image_url", "image_url": {"url": "https://example.com/test.png"}},
    ]
    with pytest.raises(UnsupportedModalityError) as exc_info:
        extract_text_from_content(content)
    assert "не поддерживается" in str(exc_info.value)


def test_single_user_message_clean():
    messages = [{"role": "user", "content": "Just a single question"}]
    res = format_messages_to_prompt(messages)
    assert res == "Just a single question"


def test_role_blocks_formatting():
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "Python is a programming language."},
        {"role": "user", "content": "Tell me more."},
    ]
    res = format_messages_to_prompt(messages, format_type="role_blocks")
    assert "[System]\nYou are a helpful assistant." in res
    assert "[User]\nWhat is Python?" in res
    assert "[Assistant]\nPython is a programming language." in res
    assert "[User]\nTell me more." in res


def test_plain_formatting():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Hi."},
    ]
    res = format_messages_to_prompt(messages, format_type="plain")
    assert "System: Be concise." in res
    assert "User: Hi." in res
    assert res.endswith("Assistant: ")


def test_chatml_formatting():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Hi."},
    ]
    res = format_messages_to_prompt(messages, format_type="chatml")
    assert "<|im_start|>system\nBe concise.<|im_end|>" in res
    assert "<|im_start|>user\nHi.<|im_end|>" in res
    assert res.endswith("<|im_start|>assistant\n")
