#!/bin/bash
# Очистка кэшей (page cache, dentries, inodes)

echo 3 | sudo -n tee /proc/sys/vm/drop_caches > /dev/null 2>&1 || echo 3 | sudo -A tee /proc/sys/vm/drop_caches > /dev/null 2>&1 || pkexec tee /proc/sys/vm/drop_caches > /dev/null 2>&1 <<< 3
echo "Кэш памяти очищен"