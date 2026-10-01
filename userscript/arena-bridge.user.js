// ==UserScript==
// @name         arena-bridge
// @namespace    https://github.com/arena-bridge
// @version      1.0.0
// @description  OpenAI-compatible WebSocket bridge for arena.ai (Termux / Android / PC)
// @author       arena-bridge
// @match        https://arena.ai/*
// @match        https://*.arena.ai/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';

    // =========================================================================
    // КОНФИГУРАЦИЯ И СЕЛЕКТОРЫ
    // =========================================================================
    const CONFIG = {
        // Адрес WebSocket-сервера arena-bridge (порт заменяется сервером автоматически)
        wsUrl: "ws://127.0.0.1:8000/ws",

        // Настройки повторного подключения
        reconnectInitialDelayMs: 2000,
        reconnectMaxDelayMs: 15000,

        // reCAPTCHA v3 параметры (из бандлов arena.ai)
        recaptchaSitekey: "6Led_uYrAAAAAKjxDIF58fgFtX3t8loNAK85bW9I",
        recaptchaAction: "chat_submit",

        // Next.js Server Action ID для удаления чата (deleteEvaluationSession)
        nextActionDeleteSession: "6000de8c877b516b9e7232815f5ab84630af0d09d7",
        nextActionArchiveSession: "602e8dbae00e42be76aee5e24dc3d871c181d3f738",

        // Внутренние эндпоинты
        endpoints: {
            createEvaluation: "/nextjs-api/stream/create-evaluation",
            modelCatalog: "/nextjs-api/model-catalog"
        },

        // DOM-селекторы (для резервного управления через интерфейс)
        selectors: {
            messageTextarea: "textarea[placeholder*='message'], textarea",
            submitButton: "button[type='submit']",
            sidebarHistoryItems: "nav a, [data-sidebar='menu-button']",
            chatMenuTrigger: "button[aria-label*='More'], button[aria-label*='menu']"
        }
    };

    // =========================================================================
    // ГЕНЕРАЦИЯ UUIDv7
    // =========================================================================
    function generateUUIDv7() {
        const timestamp = Date.now();
        const timeHex = timestamp.toString(16).padStart(12, '0');
        const randomBytes = new Uint8Array(10);
        (window.crypto || window.msCrypto).getRandomValues(randomBytes);
        const randHex = Array.from(randomBytes).map(b => b.toString(16).padStart(2, '0')).join('');

        const p1 = timeHex.slice(0, 8);
        const p2 = timeHex.slice(8, 12);
        const p3 = '7' + randHex.slice(0, 3);
        const p4 = ((parseInt(randHex.slice(3, 4), 16) & 0x3) | 0x8).toString(16) + randHex.slice(4, 7);
        const p5 = randHex.slice(7, 19);
        return `${p1}-${p2}-${p3}-${p4}-${p5}`;
    }

    // =========================================================================
    // ИНТЕРФЕЙС СТАТУСА (UI HUD)
    // =========================================================================
    let statusEl = null;

    function initUI() {
        if (document.getElementById('arena-bridge-hud')) return;

        statusEl = document.createElement('div');
        statusEl.id = 'arena-bridge-hud';
        statusEl.style.cssText = `
            position: fixed;
            bottom: 12px;
            right: 12px;
            background: rgba(15, 23, 42, 0.92);
            color: #f8fafc;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 8px 12px;
            font-family: monospace, sans-serif;
            font-size: 12px;
            z-index: 999999;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
            pointer-events: auto;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s ease;
        `;
        statusEl.innerHTML = `<span id="ab-dot" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#ef4444;"></span> <span id="ab-text">Bridge: Connecting...</span>`;
        document.body.appendChild(statusEl);
    }

    function updateUI(status, message) {
        if (!statusEl) initUI();
        const dot = document.getElementById('ab-dot');
        const text = document.getElementById('ab-text');
        if (!dot || !text) return;

        if (status === 'connected') {
            dot.style.background = '#22c55e';
            text.textContent = message || 'Bridge: Ready';
        } else if (status === 'busy') {
            dot.style.background = '#eab308';
            text.textContent = message || 'Bridge: Generating...';
        } else {
            dot.style.background = '#ef4444';
            text.textContent = message || 'Bridge: Disconnected';
        }
    }

    // =========================================================================
    // reCAPTCHA v3 ПОЛУЧЕНИЕ ТОКЕНА
    // =========================================================================
    async function getRecaptchaToken() {
        try {
            if (window.grecaptcha && typeof window.grecaptcha.execute === 'function') {
                return await window.grecaptcha.execute(CONFIG.recaptchaSitekey, { action: CONFIG.recaptchaAction });
            }
        } catch (e) {
            console.warn('[arena-bridge] Ошибка вызова grecaptcha:', e);
        }
        return null;
    }

    // =========================================================================
    // УДАЛЕНИЕ ЧАТА
    // =========================================================================
    async function deleteChatSession(sessionId) {
        if (!sessionId) return false;
        console.log(`[arena-bridge] 🗑️ Запрос на удаление сессии: ${sessionId}`);

        try {
            // Способ 1: Вызов Server Action deleteEvaluationSession
            const resp = await fetch(window.location.pathname, {
                method: "POST",
                headers: {
                    "Next-Action": CONFIG.nextActionDeleteSession,
                    "Accept": "text/x-component",
                    "Content-Type": "text/plain;charset=UTF-8"
                },
                body: JSON.stringify([sessionId])
            });

            if (resp.ok) {
                console.log(`[arena-bridge] ✅ Сессия ${sessionId} успешно удалена через Server Action.`);
                return true;
            } else {
                console.warn(`[arena-bridge] Server Action delete вернул статус ${resp.status}`);
            }
        } catch (e) {
            console.error(`[arena-bridge] Ошибка при удалении сессии ${sessionId}:`, e);
        }

        // Способ 2: Резервный вызов archiveEvaluationSession
        try {
            await fetch(window.location.pathname, {
                method: "POST",
                headers: {
                    "Next-Action": CONFIG.nextActionArchiveSession,
                    "Accept": "text/x-component",
                    "Content-Type": "text/plain;charset=UTF-8"
                },
                body: JSON.stringify([sessionId])
            });
        } catch (e) {
            // тихо игнорируем резервную попытку
        }

        return false;
    }

    // =========================================================================
    // ВЫПОЛНЕНИЕ ЗАПРОСА ВНУТРИ ARENA.AI
    // =========================================================================
    async function executeChatTask(task, ws) {
        const { id: reqId, modelId, prompt, deleteChat } = task;
        console.log(`[arena-bridge] 🚀 Старт запроса ${reqId} для модели ${modelId}`);
        updateUI('busy', `Generating (${reqId.slice(0, 6)})...`);

        const sessionId = generateUUIDv7();
        const userMsgId = generateUUIDv7();
        const modelAMsgId = generateUUIDv7();
        const modelBMsgId = generateUUIDv7();

        // Немедленно сообщаем серверу ID создаваемой сессии для гарантии удаления
        ws.send(JSON.stringify({
            type: "session_created",
            id: reqId,
            sessionId: sessionId
        }));

        let recaptchaToken = await getRecaptchaToken();

        const payload = {
            id: sessionId,
            mode: "direct",
            modelAId: modelId,
            modelBId: void 0,
            userMessageId: userMsgId,
            modelAMessageId: modelAMsgId,
            modelBMessageId: modelBMsgId,
            userMessage: {
                content: prompt,
                experimental_attachments: [],
                metadata: {}
            },
            modality: "text",
            recaptchaV3Token: recaptchaToken
        };

        try {
            const response = await fetch(CONFIG.endpoints.createEvaluation, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Accept": "*/*"
                },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                let errText = `HTTP ${response.status} ${response.statusText}`;
                try {
                    const errJson = await response.json();
                    if (errJson && (errJson.error || errJson.message)) {
                        errText = errJson.error || errJson.message;
                    }
                } catch (_) {}
                throw new Error(`Arena API Error: ${errText}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop(); // Сохраняем незавершенную строку

                for (let line of lines) {
                    line = line.trim();
                    if (!line) continue;
                    if (line.startsWith("data:")) {
                        line = line.slice(5).trim();
                    }

                    // a0:"..." - чанк текста
                    if (line.startsWith("a0:")) {
                        try {
                            const textChunk = JSON.parse(line.slice(3));
                            ws.send(JSON.stringify({
                                type: "chunk",
                                id: reqId,
                                content: textChunk
                            }));
                        } catch (e) {
                            console.warn("[arena-bridge] Ошибка парсинга a0:", e);
                        }
                    }
                    // ag:"..." - чанк reasoning / thinking
                    else if (line.startsWith("ag:")) {
                        try {
                            const reasoningChunk = JSON.parse(line.slice(3));
                            ws.send(JSON.stringify({
                                type: "chunk",
                                id: reqId,
                                reasoning: reasoningChunk
                            }));
                        } catch (e) {
                            console.warn("[arena-bridge] Ошибка парсинга ag:", e);
                        }
                    }
                    // a3:"..." - ошибка генерации
                    else if (line.startsWith("a3:")) {
                        try {
                            const errorData = JSON.parse(line.slice(3));
                            throw new Error(String(errorData));
                        } catch (e) {
                            throw new Error(line.slice(3));
                        }
                    }
                    // ad:{...} - сигнал завершения
                    else if (line.startsWith("ad:")) {
                        // Метаданные завершения
                    }
                }
            }

            // Уведомляем сервер об успешном завершении генерации
            ws.send(JSON.stringify({
                type: "done",
                id: reqId,
                sessionId: sessionId
            }));
            console.log(`[arena-bridge] ✅ Генерация ${reqId} завершена.`);

        } catch (err) {
            console.error(`[arena-bridge] ❌ Ошибка выполнения запроса ${reqId}:`, err);
            ws.send(JSON.stringify({
                type: "error",
                id: reqId,
                sessionId: sessionId,
                error: err.message || String(err)
            }));
        } finally {
            // Удаление созданного чата
            if (deleteChat !== false) {
                updateUI('busy', 'Cleaning up chat...');
                const deleted = await deleteChatSession(sessionId);
                ws.send(JSON.stringify({
                    type: "deleted",
                    id: reqId,
                    sessionId: sessionId,
                    success: deleted
                }));
            }
            updateUI('connected', 'Bridge: Ready');
        }
    }

    // =========================================================================
    // WEBSOCKET МОСТ И АВТО-ПЕРЕПОДКЛЮЧЕНИЕ
    // =========================================================================
    let socket = null;
    let reconnectDelay = CONFIG.reconnectInitialDelayMs;
    let reconnectTimeout = null;

    function connectBridge() {
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
            return;
        }

        console.log(`[arena-bridge] Подключение к ${CONFIG.wsUrl}...`);
        updateUI('disconnected', 'Bridge: Connecting...');

        try {
            socket = new WebSocket(CONFIG.wsUrl);
        } catch (e) {
            console.error('[arena-bridge] Не удалось создать WebSocket:', e);
            scheduleReconnect();
            return;
        }

        socket.onopen = function () {
            console.log('[arena-bridge] 🟢 WebSocket подключен к arena-bridge!');
            reconnectDelay = CONFIG.reconnectInitialDelayMs;
            updateUI('connected', 'Bridge: Ready');
        };

        socket.onmessage = async function (event) {
            let msg;
            try {
                msg = JSON.parse(event.data);
            } catch (e) {
                return;
            }

            // Обработка Heartbeat
            if (msg.action === 'ping') {
                socket.send(JSON.stringify({ type: 'pong' }));
                return;
            }

            // Обработка задания генерации
            if (msg.action === 'chat') {
                await executeChatTask(msg, socket);
                return;
            }

            // Обработка прямого запроса на удаление чата
            if (msg.action === 'delete_chat' && msg.sessionId) {
                const ok = await deleteChatSession(msg.sessionId);
                socket.send(JSON.stringify({
                    type: "deleted",
                    id: msg.id,
                    sessionId: msg.sessionId,
                    success: ok
                }));
                return;
            }

            // Обработка удаления списка зависших чатов
            if (msg.action === 'delete_pending' && Array.isArray(msg.sessionIds)) {
                console.log(`[arena-bridge] Удаление ${msg.sessionIds.length} отложенных чатов...`);
                const deletedList = [];
                for (const sid of msg.sessionIds) {
                    const ok = await deleteChatSession(sid);
                    if (ok) deletedList.push(sid);
                    await new Promise(r => setTimeout(r, 500)); // небольшая пауза
                }
                socket.send(JSON.stringify({
                    type: "pending_deleted",
                    sessionIds: deletedList
                }));
                return;
            }
        };

        socket.onclose = function (e) {
            console.warn('[arena-bridge] 🔴 WebSocket соединение закрыто:', e.reason || 'no reason');
            updateUI('disconnected', 'Bridge: Disconnected');
            scheduleReconnect();
        };

        socket.onerror = function (err) {
            console.error('[arena-bridge] Ошибка WebSocket:', err);
            socket.close();
        };
    }

    function scheduleReconnect() {
        if (reconnectTimeout) clearTimeout(reconnectTimeout);
        reconnectTimeout = setTimeout(() => {
            reconnectDelay = Math.min(reconnectDelay * 1.5, CONFIG.reconnectMaxDelayMs);
            connectBridge();
        }, reconnectDelay);
    }

    // Инициализация при загрузке страницы
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            initUI();
            connectBridge();
        });
    } else {
        initUI();
        connectBridge();
    }

})();
