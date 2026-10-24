#!/bin/bash
# File operations helper
operation="${1}"
target="${2}"

case "$operation" in
  count_recent)
    find "$HOME" -type f -mtime -1 2>/dev/null | wc -l
    ;;
  size_downloads)
    du -sh "$HOME/Downloads" 2>/dev/null | awk '{print $1}'
    ;;
  size_home)
    du -sh "$HOME" 2>/dev/null | awk '{print $1}'
    ;;
  temp_size)
    du -sh /tmp 2>/dev/null | awk '{print $1}'
    ;;
  recent_modified)
    find "$HOME" -type f -mtime -1 -printf "%T@ %p\n" 2>/dev/null | sort -rn | head -5 | awk '{print $2}' | xargs -I {} basename {}
    ;;
esac
