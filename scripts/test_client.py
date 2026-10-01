#!/usr/bin/env python3
"""
Скрипт для тестирования API-сервера arena-bridge.
Проверяет:
1. GET /health
2. GET /v1/models
3. POST /v1/chat/completions (stream: true)
4. POST /v1/chat/completions (stream: false)
"""

import argparse
import json
import sys
import time
import httpx


def test_health(client: httpx.Client, base_url: str):
    print("\n🔍 1. Проверка GET /health...")
    resp = client.get(f"{base_url}/health")
    print(f"Статус: HTTP {resp.status_code}")
    print(f"Ответ: {json.dumps(resp.json(), indent=2, ensure_ascii=False)}")
    if resp.status_code != 200:
        print("❌ Ошибка health check.")
        return False
    return True


def test_models(client: httpx.Client, base_url: str):
    print("\n🔍 2. Проверка GET /v1/models...")
    resp = client.get(f"{base_url}/v1/models")
    print(f"Статус: HTTP {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json().get("data", [])
        print(f"Всего моделей: {len(data)}")
        print("Первые 5 моделей:")
        for m in data[:5]:
            print(f"  - {m.get('id')} (by {m.get('owned_by')})")
        return True
    else:
        print(f"❌ Ошибка получения моделей: {resp.text}")
        return False


def test_chat_streaming(client: httpx.Client, base_url: str, model: str, prompt: str):
    print(f"\n🚀 3. Проверка POST /v1/chat/completions (STREAM: TRUE) [модель: {model}]...")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a concise AI assistant."},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
    }

    start = time.time()
    try:
        with client.stream("POST", f"{base_url}/v1/chat/completions", json=payload, timeout=180.0) as resp:
            print(f"Статус подключения: HTTP {resp.status_code}")
            if resp.status_code != 200:
                print(f"❌ Ошибка стрима: {resp.read().decode('utf-8')}")
                return False

            print("--- Начало потокового ответа ---")
            received_tokens = []
            for line in resp.iter_lines():
                line = line.strip()
                if not line:
                    continue
                if line == "data: [DONE]":
                    break
                if line.startswith("data:"):
                    raw_data = line[5:].strip()
                    try:
                        chunk = json.loads(raw_data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        reasoning = delta.get("reasoning_content")

                        if reasoning:
                            sys.stdout.write(f"\033[90m{reasoning}\033[0m")
                            sys.stdout.flush()
                        if content:
                            sys.stdout.write(content)
                            sys.stdout.flush()
                            received_tokens.append(content)
                    except Exception as e:
                        print(f"\n[Ошибка парсинга чанка: {e}] {raw_data}")

            print("\n--- Конец потокового ответа ---")
            duration = time.time() - start
            print(f"⏱️ Время стрима: {duration:.2f} сек. Получено символов: {len(''.join(received_tokens))}")
            return True

    except Exception as e:
        print(f"❌ Исключение при выполнении стрима: {e}")
        return False


def test_chat_non_stream(client: httpx.Client, base_url: str, model: str, prompt: str):
    print(f"\n🚀 4. Проверка POST /v1/chat/completions (STREAM: FALSE) [модель: {model}]...")
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }

    start = time.time()
    try:
        resp = client.post(f"{base_url}/v1/chat/completions", json=payload, timeout=180.0)
        duration = time.time() - start
        print(f"Статус: HTTP {resp.status_code} (за {duration:.2f} сек)")

        if resp.status_code == 200:
            data = resp.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            content = msg.get("content", "")
            print("--- Ответ модели ---")
            print(content)
            print("--------------------")
            return True
        else:
            print(f"❌ Ошибка: {resp.text}")
            return False

    except Exception as e:
        print(f"❌ Исключение: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Тестовый клиент для arena-bridge")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Базовый URL сервера (без /v1)")
    parser.add_argument("--api-key", default="", help="API-ключ (если пустой, берётся из config.yaml)")
    parser.add_argument("--model", default="gemini-3.1-pro-preview", help="Название модели")
    parser.add_argument("--prompt", default="Скажи 'Привет от arena-bridge!' и назови текущий год.", help="Текст запроса")
    parser.add_argument("--only-health", action="store_true", help="Проверить только health check")
    args = parser.parse_args()

    # Попытка прочитать api_key из config.yaml, если не передан
    api_key = args.api_key
    if not api_key:
        try:
            import yaml
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                api_key = cfg.get("api_key", "")
        except Exception:
            pass

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    client = httpx.Client(headers=headers, timeout=30.0)

    print(f"Подключение к arena-bridge по адресу {args.base_url}")
    if api_key:
        print(f"Используется API-ключ: {api_key[:8]}...{api_key[-4:]}")

    if not test_health(client, args.base_url):
        sys.exit(1)

    if args.only_health:
        sys.exit(0)

    test_models(client, args.base_url)
    test_chat_streaming(client, args.base_url, args.model, args.prompt)
    test_chat_non_stream(client, args.base_url, args.model, args.prompt)


if __name__ == "__main__":
    main()
