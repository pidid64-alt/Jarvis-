#!/bin/bash
count=$(ss -tulpn 2>/dev/null | tail -n +2 | wc -l)
echo "Открытых портов: ${count}."
