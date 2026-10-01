// ==UserScript==
// @name         arena-bridge
// @namespace    https://github.com/arena-bridge
// @version      1.1.0
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
        reconnectInitialDelayMs: 1500,
        reconnectMaxDelayMs: 10000,

        // Актуальный reCAPTCHA Enterprise sitekey из бандлов arena.ai
        recaptchaEnterpriseSitekey: "6LeTGMcsAAAAALuIlkVwIxaAuZA8VledA6d3Nnb0",
        // Резервный v3 sitekey
        recaptchaSitekey: "6Led_uYrAAAAAKjxDIF58fgFtX3t8loNAK85bW9I",
        recaptchaAction: "chat_submit",

        // Next.js Server Action ID для удаления чата (deleteEvaluationSession)
        nextActionDeleteSession: "6000de8c877b516b9e7232815f5ab84630af0d09d7",
        nextActionArchiveSession: "602e8dbae00e42be76aee5e24dc3d871c181d3f738",

        // Внутренние эндпоинты
        endpoints: {
            createEvaluation: "/nextjs-api/stream/create-evaluation",
            modelCatalog: "/nextjs-api/model-catalog"
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
    // ФОНОВЫЙ РЕЖИМ ДЛЯ ANDROID (Защита от засыпания вкладки)
    // =========================================================================
    let bgAudio = null;
    let bgAudioActive = false;

    function initBackgroundKeepAlive() {
        if (bgAudio) return;
        try {
            // Беззвучный WAV-аудиопоток (44 байта)
            bgAudio = new Audio("data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=");
            bgAudio.loop = true;
            bgAudio.volume = 0.001;
        } catch (e) {
            console.warn('[arena-bridge] Не удалось инициализировать Audio:', e);
        }
    }

    function toggleBackgroundKeepAlive(forcedState) {
        initBackgroundKeepAlive();
        if (!bgAudio) return;

        const shouldPlay = (forcedState !== undefined) ? forcedState : bgAudio.paused;
        if (shouldPlay) {
            bgAudio.play().then(() => {
                bgAudioActive = true;
                console.log('[arena-bridge] 🎵 Фоновый режим ВКЛ (вкладка не уснёт при переключении).');
                updateUI();
            }).catch(e => {
                console.warn('[arena-bridge] Автозапуск аудио требует первого клика по странице:', e);
            });
        } else {
            bgAudio.pause();
            bgAudioActive = false;
            updateUI();
        }
    }

    // Активируем фоновый режим по первому тапу / клику пользователя
    window.addEventListener('click', () => { if (!bgAudioActive) toggleBackgroundKeepAlive(true); }, { once: true });
    window.addEventListener('touchstart', () => { if (!bgAudioActive) toggleBackgroundKeepAlive(true); }, { once: true });

    // =========================================================================
    // ИНТЕРФЕЙС СТАТУСА (UI HUD)
    // =========================================================================
    let statusEl = null;
    let currentBridgeStatus = 'disconnected';
    let currentBridgeMessage = 'Connecting...';

    function initUI() {
        if (document.getElementById('arena-bridge-hud')) return;

        statusEl = document.createElement('div');
        statusEl.id = 'arena-bridge-hud';
        statusEl.style.cssText = `
            position: fixed;
            bottom: 12px;
            right: 12px;
            background: rgba(15, 23, 42, 0.95);
            color: #f8fafc;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 8px 12px;
            font-family: monospace, system-ui, sans-serif;
            font-size: 12px;
            z-index: 999999;
            box-shadow: 0 4px 14px rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            user-select: none;
            transition: all 0.2s ease;
        `;
        statusEl.title = "Нажмите, чтобы включить/выключить фоновый режим для Android";
        statusEl.onclick = () => toggleBackgroundKeepAlive();
        document.body.appendChild(statusEl);
        updateUI();
    }

    function updateUI(status, message) {
        if (!statusEl) initUI();
        if (status) currentBridgeStatus = status;
        if (message) currentBridgeMessage = message;

        let dotColor = '#ef4444';
        if (currentBridgeStatus === 'connected') dotColor = '#22c55e';
        else if (currentBridgeStatus === 'busy') dotColor = '#eab308';

        const bgIcon = bgAudioActive ? '🎵 Фоновый: ВКЛ' : '🔇 Фон: выкл (кликните)';

        statusEl.innerHTML = `
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${dotColor};"></span>
            <span><b>Bridge:</b> ${currentBridgeMessage}</span>
            <span style="color:#94a3b8;font-size:10px;border-left:1px solid #475569;padding-left:6px;">${bgIcon}</span>
        `;
    }

    // =========================================================================
    // reCAPTCHA Enterprise ПОЛУЧЕНИЕ ТОКЕНА
    // =========================================================================
    async function getRecaptchaToken() {
        const w = (typeof unsafeWindow !== 'undefined' && unsafeWindow) ? unsafeWindow : window;
        const grecaptcha = w.grecaptcha;

        if (!grecaptcha) {
            console.warn('[arena-bridge] grecaptcha не найден на странице');
            return null;
        }

        // 1. Проверяем grecaptcha.enterprise (актуальный на arena.ai)
        if (grecaptcha.enterprise && typeof grecaptcha.enterprise.ready === 'function') {
            try {
                const token = await new Promise((resolve) => {
                    const timer = setTimeout(() => {
                        console.warn('[arena-bridge] grecaptcha.enterprise таймаут');
                        resolve(null);
                    }, 6000);

                    grecaptcha.enterprise.ready(async () => {
                        try {
                            const res = await grecaptcha.enterprise.execute(
                                CONFIG.recaptchaEnterpriseSitekey,
                                { action: CONFIG.recaptchaAction }
                            );
                            clearTimeout(timer);
                            resolve(res);
                        } catch (err) {
                            clearTimeout(timer);
                            console.warn('[arena-bridge] grecaptcha.enterprise.execute ошибка:', err);
                            resolve(null);
                        }
                    });
                });
                if (token) return token;
            } catch (e) {
                console.warn('[arena-bridge] Ошибка enterprise reCAPTCHA:', e);
            }
        }

        // 2. Резервный вызов стандартного grecaptcha
        if (typeof grecaptcha.execute === 'function') {
            try {
                return await grecaptcha.execute(CONFIG.recaptchaSitekey, { action: CONFIG.recaptchaAction });
            } catch (e) {
                console.warn('[arena-bridge] Резервный execute вернул ошибку:', e);
            }
        }

        return null;
    }

    // =========================================================================
    // УДАЛЕНИЕ ЧАТА
    // =========================================================================
    async function deleteChatSession(sessionId) {
        if (!sessionId) return false;
        console.log(`[arena-bridge] 🗑️ Удаление сессии: ${sessionId}`);

        try {
            // Server Action deleteEvaluationSession
            const resp = await fetch(window.location.pathname, {
                method: "POST",
                credentials: "include",
                headers: {
                    "Next-Action": CONFIG.nextActionDeleteSession,
                    "Accept": "text/x-component",
                    "Content-Type": "text/plain;charset=UTF-8"
                },
                body: JSON.stringify([sessionId])
            });

            if (resp.ok) {
                console.log(`[arena-bridge] ✅ Сессия ${sessionId} успешно удалена.`);
                return true;
            } else {
                console.warn(`[arena-bridge] Server Action delete статус: ${resp.status}`);
            }
        } catch (e) {
            console.error(`[arena-bridge] Ошибка при удалении сессии ${sessionId}:`, e);
        }

        return false;
    }

    // =========================================================================
    // ВЫПОЛНЕНИЕ ЗАПРОСА ВНУТРИ ARENA.AI
    // =========================================================================
    async function executeChatTask(task, ws) {
        const { id: reqId, modelId, prompt, deleteChat } = task;
        console.log(`[arena-bridge] 🚀 Старт запроса ${reqId} [модель: ${modelId}]`);
        updateUI('busy', `Generating (${reqId.slice(0, 6)})...`);

        const sessionId = generateUUIDv7();
        const userMsgId = generateUUIDv7();
        const modelAMsgId = generateUUIDv7();
        const modelBMsgId = generateUUIDv7();

        // Сообщаем серверу ID создаваемой сессии для гарантии удаления
        ws.send(JSON.stringify({
            type: "session_created",
            id: reqId,
            sessionId: sessionId
        }));

        const recaptchaToken = await getRecaptchaToken();

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
                credentials: "include",
                headers: {
                    "Content-Type": "application/json",
                    "Accept": "*/*"
                },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                let errDetail = `HTTP ${response.status} ${response.statusText}`;
                try {
                    const errJson = await response.json();
                    errDetail = JSON.stringify(errJson);
                } catch (_) {
                    try {
                        errDetail = await response.text();
                    } catch (_) {}
                }
                throw new Error(`Arena API Error (${response.status}): ${errDetail}`);
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
                        } catch (e) {}
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
                        } catch (e) {}
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
            console.error(`[arena-bridge] ❌ Ошибка запроса ${reqId}:`, err);
            ws.send(JSON.stringify({
                type: "error",
                id: reqId,
                sessionId: sessionId,
                error: err.message || String(err)
            }));
        } finally {
            // Гарантированное удаление чата
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
            updateUI('connected', 'Ready');
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
        updateUI('disconnected', 'Connecting...');

        try {
            socket = new WebSocket(CONFIG.wsUrl);
        } catch (e) {
            scheduleReconnect();
            return;
        }

        socket.onopen = function () {
            console.log('[arena-bridge] 🟢 WebSocket подключен к arena-bridge!');
            reconnectDelay = CONFIG.reconnectInitialDelayMs;
            updateUI('connected', 'Ready');
        };

        socket.onmessage = async function (event) {
            let msg;
            try {
                msg = JSON.parse(event.data);
            } catch (e) {
                return;
            }

            // Heartbeat
            if (msg.action === 'ping') {
                socket.send(JSON.stringify({ type: 'pong' }));
                return;
            }

            // Задание генерации
            if (msg.action === 'chat') {
                await executeChatTask(msg, socket);
                return;
            }

            // Прямое удаление
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

            // Удаление отложенных
            if (msg.action === 'delete_pending' && Array.isArray(msg.sessionIds)) {
                const deletedList = [];
                for (const sid of msg.sessionIds) {
                    const ok = await deleteChatSession(sid);
                    if (ok) deletedList.push(sid);
                    await new Promise(r => setTimeout(r, 400));
                }
                socket.send(JSON.stringify({
                    type: "pending_deleted",
                    sessionIds: deletedList
                }));
                return;
            }
        };

        socket.onclose = function () {
            updateUI('disconnected', 'Disconnected');
            scheduleReconnect();
        };

        socket.onerror = function () {
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

    // Инициализация при старте
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
