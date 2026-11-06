#!/bin/bash

while true; do
  uv run ./alice-chlhl_games_bot/bot.py
  echo "Бот упал, перезапуск через 5 сек..." >&2
  sleep 5
done