#!/bin/bash

while true; do
  cd alice-chlhl_games_bot
  uv run bot.py
  echo "Бот упал, перезапуск через 5 сек..." >&2
  sleep 5
done