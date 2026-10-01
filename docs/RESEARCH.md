# Результаты исследования arena.ai (Этап 0)

Дата исследования: 01.10.2026.
Методология: Анализ исходного кода и JS-бандлов веб-интерфейса `arena.ai` (ранее LMSYS Chatbot Arena), реверс-инжиниринг сетевых запросов и открытых наработок (включая LMArenaBridge).

---

## 1. Режим Direct Chat и запуск чата с моделью

- **URL прямого режима**: 
  - Исторический URL: `https://arena.ai/?mode=direct` (возвращает HTTP 301 Moved Permanently).
  - Актуальный URL: `https://arena.ai/text/direct`.
- **Авторизация**:
  - При наличии аккаунта авторизация передаётся через куку `arena-auth-prod-v1` (JWT токен сессии) и куки Cloudflare (`__cf_bm`, `cf_clearance`, `provisional_user_id`).
  - Без авторизации (анонимный режим) сайт может требовать прохождение Cloudflare Turnstile для выдачи `provisional_user_id` и чаще выбрасывает reCAPTCHA v2 / Managed Challenge.
  - Сессии авторизованного пользователя сохраняются в историю чатов в боковой панели (Unified History).

---

## 2. Сетевые запросы при отправке сообщения

### 2.1. Создание новой оценки (New Chat / Create Evaluation)
- **URL**: `POST https://arena.ai/nextjs-api/stream/create-evaluation`
- **Заголовки**:
  - `Content-Type: application/json`
  - `Accept: */*`
  - `Referer: https://arena.ai/text/direct`
  - `Origin: https://arena.ai`
  - Куки браузера: `arena-auth-prod-v1`, `cf_clearance`, `__cf_bm`, `provisional_user_id`
- **Тело запроса (JSON)**:
  ```json
  {
    "id": "019c7820-5480-78b6-9fef-04c0d7004054",
    "mode": "direct",
    "modelAId": "019c7820-5480-78b6-9fef-04c0d7004054",
    "modelBId": null,
    "userMessageId": "019c7820-5480-78b6-9fef-04c0d7004055",
    "modelAMessageId": "019c7820-5480-78b6-9fef-04c0d7004056",
    "modelBMessageId": "019c7820-5480-78b6-9fef-04c0d7004057",
    "userMessage": {
      "content": "Текст сообщения пользователя",
      "experimental_attachments": [],
      "metadata": {}
    },
    "modality": "text",
    "recaptchaV3Token": "<токен grecaptcha>"
  }
  ```
- **Идентификаторы ID**:
  - Все ID генерируются на клиенте в формате **UUIDv7** (миллисекунды Unix Epoch + случайные биты).
- **Капча (reCAPTCHA v3)**:
  - Action: `"chat_submit"`
  - Sitekey: `6Led_uYrAAAAAKjxDIF58fgFtX3t8loNAK85bW9I` (дополнительный/резервный: `6Led_uYrAAAAAIP_9E8Ais_67Z6Vp4vdf40p8SQU`).
  - При вызове из Userscript в контексте страницы вызывается нативный `window.grecaptcha.execute(sitekey, { action: "chat_submit" })`.

### 2.2. Формат стриминга ответов (Vercel AI SDK Data Stream)
Сервер `arena.ai` возвращает стрим в формате Vercel AI SDK с префиксами:
- `a0:"<строка>"` — текстовый чанк ответа (JSON-экранированная строка).
- `ag:"<строка>"` — чанк цепочки рассуждений (reasoning / thinking content, для reasoning-моделей вроде Grok, o1, Gemini Thinking).
- `a2:"<строка>"` — URL сгенерированного изображения / медиа (если применимо).
- `ac:"<json>"` — данные цитирования (citations / sources).
- `a3:"<ошибка>"` — сообщение об ошибке генерации.
- `ad:{"finishReason":"stop",...}` — метаданные завершения генерации.

---

## 3. Каталог моделей

- **URL**: `GET https://arena.ai/nextjs-api/model-catalog`
- **Авторизация**: Не требуется (публичный эндпоинт, требует только браузерный User-Agent для прохождения базового фильтра Cloudflare).
- **Формат ответа**: JSON массив категорий (текст, код, изображения, видео, документы):
  ```json
  [
    {
      "arena": "text",
      "models": [
        {
          "id": "019c7820-5480-78b6-9fef-04c0d7004054",
          "publicName": "gemini-3.1-pro-preview",
          "displayName": "gemini-3.1-pro-preview",
          "organization": "google",
          "userSelectable": true,
          "capabilities": {
            "inputCapabilities": { "text": true, "image": true, "file": true },
            "outputCapabilities": { "text": true, "web": true }
          }
        }
      ]
    }
  ]
  ```
- **Маппинг**: Сервер может кэшировать каталог и резолвить входящее имя модели от OpenAI клиента (`model: "gemini-3.1-pro-preview"` или `"gemini-3.6-flash"`) в UUID модели (`modelAId`).

---

## 4. Удаление чатов

На `arena.ai` удаление чатов реализовано через **Next.js Server Actions**:

1. **Серверный экшен**:
   - Название функции: `deleteEvaluationSession`
   - Актуальный `Next-Action` ID: `6000de8c877b516b9e7232815f5ab84630af0d09d7`
   - Запрос: `POST /text/direct` (или текущий путь страницы)
   - Заголовки:
     - `Next-Action: 6000de8c877b516b9e7232815f5ab84630af0d09d7`
     - `Accept: text/x-component`
     - `Content-Type: text/plain;charset=UTF-8`
   - Тело запроса: `["<evaluationSessionId>"]`
   - Результат: `{ "success": true }` или `{ "success": false, "error": "..." }`.

2. **Динамическое обнаружение Action ID**:
   - При сборках Next.js хэши Server Actions могут обновляться. Userscript может динамически находить актуальный ID `deleteEvaluationSession` в кэшированных скриптах или памяти webpack/turbopack.

3. **DOM-управление (Резервный вариант)**:
   - В боковой панели истории (Unified History) каждый чат представлен элементом списка с кнопкой меню (иконка `MoreHorizontal`).
   - Клик по кнопке открывает выпадающее меню (`DropdownMenu`), содержащее пункты "Archive" и "Delete".
   - Нажатие на "Delete" вызывает диалог подтверждения (`AlertDialogAction`), клик по которому удаляет чат.

---

## 5. Ограничения и риски

1. **Cloudflare WAF & Managed Challenges**:
   - Прямые HTTP-запросы из консоли/скриптов быстро блокируются Cloudflare Turnstile/Managed Challenge (проверено: простой curl получает страницу `<title>Just a moment...</title>`).
   - Использование Userscript внутри живого браузера (Firefox/Kiwi) решает эту проблему, так как браузер хранит валидные TLS-отпечатки, куки и контекст.
2. **Лимиты частоты (Rate Limits)**:
   - Слишком частые запросы вызывают HTTP 429 Too Many Requests. Необходима внутренняя очередь (FIFO) на сервере с задержкой между запросами (`min_request_interval`).
3. **Фоновые вкладки в мобильном Android**:
   - Android агрессивно «усыпляет» фоновые вкладки браузера. Необходим постоянный WebSocket heartbeat (ping/pong каждые 10-15 секунд) и рекомендация пользователю отключить оптимизацию батареи для браузера и Termux.
4. **Вложения и изображения**:
   - В текущей версии поддерживается только текстовый ввод (`modality: "text"`). Если клиент передаёт `image_url`, возвращаем понятную ошибку (в соответствии с ТЗ).

---

## 6. Что может сломаться и меры защиты

| Потенциальная проблема | Вероятность | Способ защиты |
|---|---|---|
| Смена ID Server Action `deleteEvaluationSession` при обновлении сайта | Средняя | Автопоиск Action ID в бандлах Next.js + резервное удаление через DOM/Sidebar |
| Смена Sitekey reCAPTCHA v3 | Низкая | Чтение `window.grecaptcha` или парсинг страницы при запуске |
| Разрыв WebSocket между сервером и userscript | Высокая | Автоматический reconnect с экспоненциальной задержкой в userscript |
| Сбой удаления чата при обрыве связи | Средняя | Локальная очередь недоудалённых ID (`failed_deletions.json`) с повтором при следующем запросе / таймере |
| Неизвестная модель от клиента | Средняя | Проверка по кэшированному каталогу моделей + возврат 404 OpenAI формата |
