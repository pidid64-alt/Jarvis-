#!/bin/bash
# Топ-5 самых больших файлов в домашней папке
echo "Самые большие файлы в домашней папке:"
find "$HOME" -type f -size +100M 2>/dev/null -exec du -h {} + 2>/dev/null | sort -rh | head -5 | while read size file; do
    basename=$(basename "$file")
    echo "${basename}: ${size}."
done

# Если больших файлов не найдено
if [ -z "$(find "$HOME" -type f -size +100M 2>/dev/null | head -1)" ]; then
    echo "Больших файлов более 100 мегабайт не найдено."
fi
