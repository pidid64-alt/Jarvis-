#!/bin/bash
# Ищет git-репозитории с незакоммиченными изменениями.
# По умолчанию смотрит в $HOME на глубину 2 — поправь SEARCH_DIRS под
# свою реальную структуру проектов, если она отличается.
SEARCH_DIRS="${JARVIS_GIT_SEARCH_DIRS:-$HOME}"
count=0
names=""
while IFS= read -r gitdir; do
  repo="$(dirname "$gitdir")"
  if ! git -C "$repo" diff --quiet 2>/dev/null || ! git -C "$repo" diff --cached --quiet 2>/dev/null; then
    count=$((count + 1))
    names="${names}$(basename "$repo"), "
  fi
done < <(find "$SEARCH_DIRS" -maxdepth 3 -type d -name ".git" 2>/dev/null)

if [ "$count" -eq 0 ]; then
  echo "Незакоммиченных изменений не нашёл."
else
  echo "Грязных репозиториев: ${count}. Это ${names%, }."
fi
