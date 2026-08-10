#!/bin/bash
# Показать открытые порты и какие процессы их держат

echo "Открытые TCP порты:"
ss -tlnp 2>/dev/null | grep LISTEN | while read line; do
    port=$(echo "$line" | awk '{print $4}' | sed 's/.*://')
    proc=$(echo "$line" | awk '{print $6}' | sed 's/.*pid=\([0-9]*\).*/\1/' | head -1)
    if [ -n "$proc" ] && [ "$proc" != "(" ]; then
        proc_name=$(ps -p "$proc" -o comm= 2>/dev/null)
        echo "  Порт $port -> $proc_name (PID $proc)"
    else
        echo "  Порт $port"
    fi
done

echo ""
echo "Открытые UDP порты:"
ss -ulnp 2>/dev/null | awk 'NR>1{port=$5; gsub(/.*:/,"",port); proc=$7; gsub(/.*pid=/,"",proc); gsub(/,.*/,"",proc); if(proc ~ /^[0-9]+$/) {cmd="ps -p " proc " -o comm="; cmd | getline proc_name; close(cmd); if(proc_name!="") print "  Порт " port " -> " proc_name " (PID " proc ")"; else print "  Порт " port " -> PID " proc} else print "  Порт " port}'