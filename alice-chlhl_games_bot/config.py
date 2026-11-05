# -*- coding: utf-8 -*-
"""Конфигурация бота ЧЛХЛ."""
import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "chlhl.db")

# Время планового обновления данных (в локальном времени сервера)
UPDATE_TIMES = [(8, 0), (13, 0), (18, 0), (23, 0), (23, 55)]

GAMES_PER_PAGE = 10
