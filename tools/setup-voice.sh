#!/usr/bin/env bash
# Установка распознавания голосовых: whisper.cpp + модель. Для Linux и macOS.
set -euo pipefail
cd "$(dirname "$0")/.."

HOME_DIR="tools/whisper"
MODEL="${1:-small}"

say()  { printf "  %s\n" "$1"; }
ok()   { printf "  \033[32m[OK]\033[0m %s\n" "$1"; }
fail() { printf "\n  \033[31m[X] %s\033[0m\n\n" "$1" >&2; exit 1; }

echo
say "Ставлю распознавание голосовых (whisper.cpp)"
say "Папка: $HOME_DIR"
echo
mkdir -p "$HOME_DIR"

# --------------------------------------------------------------- whisper.cpp

if find "$HOME_DIR" -name whisper-cli -o -name main 2>/dev/null | grep -q .; then
    ok "whisper.cpp уже на месте"
else
    command -v cmake >/dev/null || fail "Нужен cmake: sudo apt install cmake build-essential"
    say "Собираю whisper.cpp из исходников (пара минут)…"
    rm -rf "$HOME_DIR/src"
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$HOME_DIR/src" >/dev/null 2>&1 \
        || fail "Не смог скачать исходники whisper.cpp"
    cmake -B "$HOME_DIR/src/build" -S "$HOME_DIR/src" -DCMAKE_BUILD_TYPE=Release >/dev/null \
        && cmake --build "$HOME_DIR/src/build" --config Release -j >/dev/null \
        || fail "Сборка не удалась"
    ok "whisper.cpp собран"
fi

# --------------------------------------------------------------------- модель

if find "$HOME_DIR" -name 'ggml-*.bin' 2>/dev/null | grep -q .; then
    ok "Модель уже на месте"
else
    say "Скачиваю модель ggml-$MODEL.bin (100–500 МБ)…"
    curl -fL --progress-bar \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL.bin" \
        -o "$HOME_DIR/ggml-$MODEL.bin" || fail "Не смог скачать модель"
    ok "Модель: ggml-$MODEL.bin"
fi

# --------------------------------------------------------------------- ffmpeg

command -v ffmpeg >/dev/null \
    && ok "ffmpeg на месте" \
    || fail "Нужен ffmpeg: sudo apt install ffmpeg (или brew install ffmpeg)"

echo
say "Готово. Перезапусти бота — в шапке появится «Голос ✓»."
echo
