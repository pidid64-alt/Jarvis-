#!/bin/bash
# Информация о PCI устройствах (видеокарты, контроллеры и т.д.)

echo "PCI устройства:"
lspci -v 2>/dev/null | grep -E '(VGA|Audio|Network|USB|SATA|NVMe)' | head -20