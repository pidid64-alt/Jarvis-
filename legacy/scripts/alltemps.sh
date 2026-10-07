#!/bin/bash
# Информация о температуре всех датчиков

sensors 2>/dev/null | grep -E '(Core|Package|temp|CPU|GPU)' | head -20 || echo "sensors не установлен или датчики недоступны"