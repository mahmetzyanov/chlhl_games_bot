#!/bin/bash
while true; do
  uv run bot.py
  echo "Бот упал, перезапуск через 5 сек..." >&2
  sleep 5
done
