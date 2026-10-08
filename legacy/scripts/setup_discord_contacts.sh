#!/bin/bash
# Discord Friend Contact Manager
# Allows adding custom voice commands for Discord friends

CONTACTS_FILE="$HOME/jarvis/discord_contacts.json"

# Initialize contacts file if it doesn't exist
if [ ! -f "$CONTACTS_FILE" ]; then
    echo '{
  "contacts": [
    {
      "name": "fen",
      "display_name": "Фен",
      "username": "fen"
    },
    {
      "name": "alex",
      "display_name": "Алекс",
      "username": "alex"
    },
    {
      "name": "dima",
      "display_name": "Дима",
      "username": "dima"
    },
    {
      "name": "max",
      "display_name": "Макс",
      "username": "max"
    },
    {
      "name": "ivan",
      "display_name": "Иван",
      "username": "ivan"
    },
    {
      "name": "kate",
      "display_name": "Катя",
      "username": "kate"
    }
  ]
}' > "$CONTACTS_FILE"
fi

echo "Discord контакты настроены: $CONTACTS_FILE"
echo "Добавь своих друзей в этот файл для быстрых звонков!"
