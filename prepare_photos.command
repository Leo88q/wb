#!/bin/bash
# Двойной клик по этому файлу на macOS подготовит фото из /Users/zlata/Desktop/Одежда
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3)

if ! "$PY" -c 'import PIL' >/dev/null 2>&1; then
  echo "Один раз устанавливаю библиотеку для сжатия фото (Pillow)…"
  "$PY" -m pip install --user --quiet Pillow pillow-heif >/dev/null 2>&1 || \
  "$PY" -m pip install --user --quiet --break-system-packages Pillow pillow-heif >/dev/null 2>&1 || \
  echo "Не удалось поставить Pillow — использую встроенный sips (режим --engine sips)"
fi

"$PY" tools/prepare_photos.py \
  --input "/Users/zlata/Desktop/Одежда" \
  --output "/Users/zlata/Desktop/Одежда_WB" \
  "$@"

echo
read -r -p "Готово. Нажмите Enter, чтобы закрыть окно. " _
