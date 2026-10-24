#!/bin/bash
# Информация о подключенных USB устройствах

echo "Подключенные USB устройства:"
lsusb -t 2>/dev/null | grep -v "Root Hub" | awk '{print "  " $0}'