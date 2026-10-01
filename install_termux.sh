#!/bin/bash
# ==============================================================================
# Скрипт установки arena-bridge для Android Termux (без root)
# ==============================================================================

set -e

echo "=================================================="
echo "🚀 Установка arena-bridge в Termux"
echo "=================================================="

# 0. Проверка расположения проекта
CURRENT_DIR=$(pwd)
if [[ "$CURRENT_DIR" == /sdcard* ]] || [[ "$CURRENT_DIR" == /storage* ]]; then
    echo "❌ ВНИМАНИЕ: Папка проекта находится в общей памяти телефона ($CURRENT_DIR)!"
    echo "Файловая система Android в этой области блокирует выполнение скриптов и создание venv."
    echo ""
    echo "Пожалуйста, переместите папку в изолированную память Termux:"
    echo "  cp -r $(pwd) ~/"
    echo "  cd ~/$(basename "$CURRENT_DIR")"
    echo "  ./install_termux.sh"
    echo "=================================================="
    exit 1
fi

# 1. Обновление репозиториев Termux и установка пакетов
echo "📦 [1/4] Обновление пакетов и установка Python..."
if command -v pkg >/dev/null 2>&1; then
    pkg update -y
    pkg install -y python python-pip git termux-tools
else
    echo "⚠️ Команда pkg не найдена (не Termux?). Пропускаем pkg update."
fi

# 2. Настройка виртуального окружения Python
echo "🐍 [2/4] Настройка окружения Python..."
# Очищаем повреждённый venv, если он остался от прошлых попыток
rm -rf venv 2>/dev/null || true

USE_VENV=false
if python3 -m venv venv 2>/dev/null && [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
    USE_VENV=true
    echo "✅ Виртуальное окружение venv успешно создано и активировано."
else
    rm -rf venv 2>/dev/null || true
    echo "ℹ️ venv не поддерживается или не требуется. Установка выполняется в системный Termux Python."
fi

# 3. Установка легковесных зависимостей Python
echo "📥 [3/4] Установка Python-зависимостей из requirements.txt..."
if [ "$USE_VENV" = true ]; then
    pip install --upgrade pip 2>/dev/null || true
    pip install -r requirements.txt
else
    pip install --upgrade pip 2>/dev/null || true
    pip install -r requirements.txt --break-system-packages 2>/dev/null || pip install -r requirements.txt
fi

# 4. Проверка и создание файла конфигурации
echo "⚙️ [4/4] Настройка конфигурации..."
if [ ! -f "config.yaml" ]; then
    if [ -f "config.example.yaml" ]; then
        cp config.example.yaml config.yaml
        echo "✅ Файл config.yaml создан из примера config.example.yaml."
    fi
else
    echo "ℹ️ Файл config.yaml уже существует."
fi

# Настройка прав на запуск
chmod +x run.sh scripts/test_client.py 2>/dev/null || true

echo ""
echo "=================================================="
echo "🎉 Установка успешно завершена!"
echo "Для запуска сервера выполните: ./run.sh"
echo "=================================================="
