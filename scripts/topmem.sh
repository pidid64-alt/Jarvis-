#!/bin/bash
# Топ-5 процессов по памяти

echo "Топ-5 процессов по памяти:"
ps aux --sort=-%mem | head -6 | awk 'NR==1{print $0} NR>1{printf "%s (PID %s) - %.1f%% памяти\n", $11, $2, $4}'