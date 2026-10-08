#!/bin/bash
ps -eo comm,%cpu --sort=-%cpu --no-headers | head -3 | awk '{printf "%s: %s процентов. ", $1, $2}'
echo
