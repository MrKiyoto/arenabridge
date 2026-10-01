#!/bin/bash
# ==============================================================================
# Скрипт запуска arena-bridge в Termux / Linux
# ==============================================================================

# Функция очистки при завершении скрипта (Ctrl+C или kill)
cleanup() {
    echo ""
    echo "🛑 Остановка arena-bridge..."
    if command -v termux-wake-unlock >/dev/null 2>&1; then
        echo "💤 Освобождение termux-wake-lock..."
        termux-wake-unlock || true
    fi
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# 1. Захват блокировки сна Termux (защита от убийства фонового процесса Android)
if command -v termux-wake-lock >/dev/null 2>&1; then
    echo "🔋 Активация termux-wake-lock (процесс защищён от засыпания Android)..."
    termux-wake-lock || true
fi

# 2. Активация виртуального окружения (если создано)
if [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

# 3. Запуск сервера Python
echo "🚀 Запуск сервера arena-bridge..."
python3 -m server
