"""
Модуль каталога моделей arena.ai.
Загружает и кэширует список моделей с arena.ai/nextjs-api/model-catalog,
поддерживает переопределение из конфига и сопоставляет входящие имена моделей с их UUID.
"""

import logging
import time
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger("arena_bridge.models")

CATALOG_URL = "https://arena.ai/nextjs-api/model-catalog"
CACHE_TTL_SECONDS = 3600  # 1 час

# Базовый резервный список популярных моделей на случай отсутствия сети при старте
FALLBACK_MODELS = [
    {
        "id": "019c7820-5480-78b6-9fef-04c0d7004054",
        "publicName": "gemini-3.1-pro-preview",
        "displayName": "gemini-3.1-pro-preview",
        "organization": "google",
    },
    {
        "id": "019f90b1-c0ac-71ce-b295-487f261bf0f4",
        "publicName": "gemini-3.6-flash",
        "displayName": "gemini-3.6-flash",
        "organization": "google",
    },
    {
        "id": "019e71ea-1e1d-740f-9c2d-dab5869ff108",
        "publicName": "gpt-5.5-instant",
        "displayName": "gpt-5.5-instant",
        "organization": "openai",
    },
    {
        "id": "019b47da-49b9-7295-906c-ce44ccd30d74",
        "publicName": "gemini-3-flash",
        "displayName": "gemini-3-flash",
        "organization": "google",
    },
    {
        "id": "019ce35a-fa6f-7262-8ee2-ed4442821ce7",
        "publicName": "grok-4.20-beta-0309-reasoning",
        "displayName": "grok-4.20-beta-0309-reasoning",
        "organization": "xai",
    },
    {
        "id": "019a8548-a2b1-70ce-b1be-eba096d41f58",
        "publicName": "gpt-5.1-high",
        "displayName": "gpt-5.1-high",
        "organization": "openai",
    },
    {
        "id": "019c45d7-96f0-7d39-8143-9d57941b5523",
        "publicName": "glm-5",
        "displayName": "glm-5",
        "organization": "zhipu",
    },
]


class ModelCatalog:
    def __init__(self, models_override: Optional[List[Dict[str, Any]]] = None):
        self._models_override = models_override or []
        self._cached_models: List[Dict[str, Any]] = []
        self._last_fetched: float = 0.0
        # Инициализируем резервными моделями
        self._cached_models = list(FALLBACK_MODELS)

    async def get_models(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Возвращает актуальный список моделей с arena.ai (с учетом кэширования)."""
        now = time.time()
        if not force_refresh and self._cached_models and (now - self._last_fetched < CACHE_TTL_SECONDS):
            return self._apply_overrides(self._cached_models)

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json",
            }
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(CATALOG_URL, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    extracted_models = []
                    for section in data:
                        # Фильтруем текстовые модели
                        if section.get("arena") in ("text", "code", "document"):
                            for m in section.get("models", []):
                                if m.get("userSelectable"):
                                    extracted_models.append(m)

                    if extracted_models:
                        # Удаляем дубликаты по id
                        unique_models = {}
                        for m in extracted_models:
                            unique_models[m["id"]] = m
                        self._cached_models = list(unique_models.values())
                        self._last_fetched = now
                        logger.info(f"Каталог моделей обновлен: загружено {len(self._cached_models)} моделей.")
        except Exception as e:
            logger.warning(f"Не удалось загрузить каталог моделей с arena.ai: {e}. Используется кэш/резерв.")

        return self._apply_overrides(self._cached_models)

    def _apply_overrides(self, base_models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Применяет ручные переопределения моделей из конфига."""
        if not self._models_override:
            return base_models

        result = {m["id"]: m for m in base_models}
        for item in self._models_override:
            m_id = item.get("id") or item.get("name")
            if m_id:
                result[m_id] = {
                    "id": m_id,
                    "publicName": item.get("name", m_id),
                    "displayName": item.get("name", m_id),
                    "organization": item.get("organization", "custom"),
                }
        return list(result.values())

    async def resolve_model_id(self, model_name: str) -> Optional[str]:
        """
        Сопоставляет переданное клиентом имя (publicName, displayName или UUID)
        с фактическим UUID модели на arena.ai.
        """
        if not model_name:
            return None

        clean_name = model_name.strip().lower()
        models = await self.get_models()

        # 1. Точное совпадение по UUID
        for m in models:
            if m.get("id", "").lower() == clean_name:
                return m["id"]

        # 2. Точное совпадение по publicName
        for m in models:
            if m.get("publicName", "").lower() == clean_name:
                return m["id"]

        # 3. Точное совпадение по displayName
        for m in models:
            if m.get("displayName", "").lower() == clean_name:
                return m["id"]

        # 4. Частичное совпадение (префикс или суффикс)
        for m in models:
            pub = m.get("publicName", "").lower()
            if clean_name in pub or pub in clean_name:
                return m["id"]

        return None

    def format_openai_models_list(self, models: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Преобразует список моделей в формат OpenAI GET /v1/models."""
        data = []
        for m in models:
            data.append({
                "id": m.get("publicName") or m.get("id"),
                "object": "model",
                "created": 1700000000,
                "owned_by": m.get("organization", "arena.ai"),
                "permission": [],
                "root": m.get("id"),
                "parent": None,
            })
        return {
            "object": "list",
            "data": data,
        }
