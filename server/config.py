"""
Модуль конфигурации arena-bridge.
Загружает настройки из config.yaml, генерирует API-ключ при первом запуске
и валидирует параметры сервера.
"""

import os
import secrets
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

logger = logging.getLogger("arena_bridge.config")

# Дефолтные настройки
DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8000,
    "api_key": "",
    "max_concurrency": 1,
    "request_timeout": 180,
    "min_request_interval": 1.5,
    "delete_chats": True,
    "prompt_format": "role_blocks",
    "models_override": [],
    "log_level": "INFO",
    "failed_deletions_file": "failed_deletions.json",
}

CONFIG_FILENAME = "config.yaml"
EXAMPLE_CONFIG_FILENAME = "config.example.yaml"


class Config:
    def __init__(self, data: Dict[str, Any], config_path: Path):
        self._data = data
        self.config_path = config_path

    @property
    def host(self) -> str:
        return str(self._data.get("host", DEFAULT_CONFIG["host"])).strip()

    @property
    def port(self) -> int:
        return int(self._data.get("port", DEFAULT_CONFIG["port"]))

    @property
    def api_key(self) -> str:
        return str(self._data.get("api_key", "")).strip()

    @property
    def max_concurrency(self) -> int:
        return max(1, int(self._data.get("max_concurrency", DEFAULT_CONFIG["max_concurrency"])))

    @property
    def request_timeout(self) -> int:
        return max(10, int(self._data.get("request_timeout", DEFAULT_CONFIG["request_timeout"])))

    @property
    def min_request_interval(self) -> float:
        return max(0.0, float(self._data.get("min_request_interval", DEFAULT_CONFIG["min_request_interval"])))

    @property
    def delete_chats(self) -> bool:
        return bool(self._data.get("delete_chats", DEFAULT_CONFIG["delete_chats"]))

    @property
    def prompt_format(self) -> str:
        fmt = str(self._data.get("prompt_format", DEFAULT_CONFIG["prompt_format"])).lower()
        if fmt not in ("role_blocks", "plain", "chatml"):
            return "role_blocks"
        return fmt

    @property
    def models_override(self) -> List[Dict[str, Any]]:
        val = self._data.get("models_override", [])
        return val if isinstance(val, list) else []

    @property
    def log_level(self) -> str:
        return str(self._data.get("log_level", DEFAULT_CONFIG["log_level"])).upper()

    @property
    def failed_deletions_file(self) -> str:
        return str(self._data.get("failed_deletions_file", DEFAULT_CONFIG["failed_deletions_file"]))


def get_base_dir() -> Path:
    """Возвращает корневую директорию проекта."""
    return Path(__file__).resolve().parent.parent


def load_config(custom_path: Optional[str] = None) -> Config:
    """
    Загружает конфигурацию из файла.
    Если файла нет, создаёт его из примера и генерирует надёжный API-ключ.
    """
    base_dir = get_base_dir()
    config_path = Path(custom_path) if custom_path else base_dir / CONFIG_FILENAME
    example_path = base_dir / EXAMPLE_CONFIG_FILENAME

    data: Dict[str, Any] = {}

    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Ошибка при чтении {config_path}: {e}. Используются значения по умолчанию.")
            data = {}
    elif example_path.exists():
        # Скопировать из example
        try:
            with open(example_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Не удалось прочитать {example_path}: {e}")
            data = dict(DEFAULT_CONFIG)
    else:
        data = dict(DEFAULT_CONFIG)

    # Применяем значения по умолчанию для отсутствующих полей
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)

    # Проверяем наличие API-ключа
    api_key = str(merged.get("api_key", "")).strip()
    if not api_key:
        generated_key = f"sk-arena-{secrets.token_hex(16)}"
        merged["api_key"] = generated_key
        print("=" * 60)
        print("🔑 ВНИМАНИЕ: API-ключ не был задан в конфигурации.")
        print(f"Сгенерирован новый случайный ключ: {generated_key}")
        print(f"Ключ сохранён в {config_path.name}")
        print("=" * 60)

        # Сохраняем обновленный конфиг
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(merged, f, default_flow_style=False, allow_unicode=True)
        except Exception as e:
            logger.warning(f"Не удалось автоматически сохранить ключ в {config_path}: {e}")

    # Проверка безопасности хоста
    if merged.get("host") == "0.0.0.0":
        print("⚠️ ПРЕДУПРЕЖДЕНИЕ БЕЗОПАСНОСТИ: Сервер настроен на прослушивание 0.0.0.0 (все сетевые интерфейсы)!")
        print("Убедитесь, что API-ключ надёжен и защищён от несанкционированного доступа.")

    return Config(merged, config_path)
