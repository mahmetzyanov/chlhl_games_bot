# -*- coding: utf-8 -*-
"""SQLite-слой для бота ЧЛХЛ.

Таблицы:
- divisions      — дивизионы (турниры)
- teams          — команды (в привязке к дивизиону)
- stadiums       — стадионы и адреса
- games_past     — результаты сыгранных игр
- games_upcoming — предстоящие игры
- subscriptions  — подписки пользователей (на дивизионы и команды)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Iterable, Optional

from parser import Match, Team, Division

SCHEMA = """
CREATE TABLE IF NOT EXISTS divisions (
    tournament_id INTEGER PRIMARY KEY,
    name          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    team_id     INTEGER NOT NULL,
    division_id INTEGER NOT NULL,
    name        TEXT NOT NULL,
    city        TEXT,
    PRIMARY KEY (team_id, division_id),
    FOREIGN KEY (division_id) REFERENCES divisions(tournament_id)
);

CREATE TABLE IF NOT EXISTS stadiums (
    name    TEXT PRIMARY KEY,
    address TEXT
);

CREATE TABLE IF NOT EXISTS games_past (
    match_id        INTEGER PRIMARY KEY,
    division_id     INTEGER NOT NULL,
    game_date       TEXT,
    game_time       TEXT,
    round           TEXT,
    home_team       TEXT,
    home_team_id    INTEGER,
    away_team       TEXT,
    away_team_id    INTEGER,
    home_score      INTEGER,
    away_score      INTEGER,
    stadium         TEXT,
    stadium_address TEXT
);
CREATE INDEX IF NOT EXISTS idx_past_div ON games_past(division_id, game_date);
CREATE INDEX IF NOT EXISTS idx_past_team ON games_past(home_team_id, away_team_id);

CREATE TABLE IF NOT EXISTS games_upcoming (
    match_id        INTEGER PRIMARY KEY,
    division_id     INTEGER NOT NULL,
    game_date       TEXT,
    game_time       TEXT,
    round           TEXT,
    home_team       TEXT,
    home_team_id    INTEGER,
    away_team       TEXT,
    away_team_id    INTEGER,
    stadium         TEXT,
    stadium_address TEXT
);
CREATE INDEX IF NOT EXISTS idx_up_div ON games_upcoming(division_id, game_date);
CREATE INDEX IF NOT EXISTS idx_up_team ON games_upcoming(home_team_id, away_team_id);

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id  INTEGER NOT NULL,
    chat_id  INTEGER NOT NULL,
    kind     TEXT NOT NULL CHECK (kind IN ('division', 'team')),
    ref_id   INTEGER NOT NULL,
    title    TEXT NOT NULL,
    PRIMARY KEY (user_id, kind, ref_id)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------- хранения

def save_divisions(conn: sqlite3.Connection, divisions: list[Division]) -> None:
    for d in divisions:
        conn.execute(
            "INSERT INTO divisions(tournament_id, name) VALUES(?,?) "
            "ON CONFLICT(tournament_id) DO UPDATE SET name=excluded.name",
            (d.tournament_id, d.name),
        )
    conn.commit()


def save_teams(conn: sqlite3.Connection, teams: list[Team]) -> None:
    for t in teams:
        conn.execute(
            "INSERT INTO teams(team_id, division_id, name, city) VALUES(?,?,?,?) "
            "ON CONFLICT(team_id, division_id) DO UPDATE SET name=excluded.name, city=excluded.city",
            (t.team_id, t.division_id, t.name, t.city),
        )
    conn.commit()


def save_stadiums(conn: sqlite3.Connection, stadiums: dict[str, str]) -> None:
    for name, addr in stadiums.items():
        conn.execute(
            "INSERT INTO stadiums(name, address) VALUES(?,?) "
            "ON CONFLICT(name) DO UPDATE SET address=excluded.address",
            (name, addr),
        )
    conn.commit()


def _match_row(m: Match) -> tuple:
    return (
        m.match_id, m.division_id, m.game_date, m.game_time, m.round,
        m.home_name, m.home_team_id, m.away_name, m.away_team_id,
        m.stadium, m.stadium_address,
    )


def _played_signature(row: sqlite3.Row) -> tuple:
    return (row["home_score"], row["away_score"])


def _upcoming_signature(row: sqlite3.Row) -> tuple:
    return (row["game_date"], row["game_time"], row["round"], row["stadium"], row["stadium_address"])


def sync_matches(
    conn: sqlite3.Connection, matches: list[Match]
) -> tuple[list[Match], list[Match]]:
    """Синхронизирует матчи с БД.

    Возвращает (новые_результаты, изменения/новые_будущие_игры) — события
    для рассылки подписчикам. Сыгранный матч переносится из games_upcoming
    в games_past.
    """
    new_results: list[Match] = []
    new_or_changed_games: list[Match] = []

    past_ids = {
        r["match_id"]: r for r in conn.execute("SELECT * FROM games_past")
    }
    up_ids = {
        r["match_id"]: r for r in conn.execute("SELECT * FROM games_upcoming")
    }

    for m in matches:
        if m.is_played:
            old = past_ids.get(m.match_id)
            if old is None or _played_signature(old) != (m.home_score, m.away_score):
                conn.execute(
                    """INSERT INTO games_past(match_id, division_id, game_date, game_time, round,
                       home_team, home_team_id, away_team, away_team_id, home_score, away_score,
                       stadium, stadium_address)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(match_id) DO UPDATE SET
                         game_date=excluded.game_date, game_time=excluded.game_time,
                         round=excluded.round, home_team=excluded.home_team,
                         home_team_id=excluded.home_team_id, away_team=excluded.away_team,
                         away_team_id=excluded.away_team_id, home_score=excluded.home_score,
                         away_score=excluded.away_score, stadium=excluded.stadium,
                         stadium_address=excluded.stadium_address""",
                    # _match_row даёт 11 значений; счёт вставляем между away_team_id и stadium
                    _match_row(m)[:9] + (m.home_score, m.away_score) + _match_row(m)[9:],
                )
                new_results.append(m)
            if m.match_id in up_ids:
                conn.execute("DELETE FROM games_upcoming WHERE match_id=?", (m.match_id,))
        else:
            old = up_ids.get(m.match_id)
            if old is None:
                conn.execute(
                    """INSERT INTO games_upcoming(match_id, division_id, game_date, game_time, round,
                       home_team, home_team_id, away_team, away_team_id, stadium, stadium_address)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    _match_row(m),
                )
                new_or_changed_games.append(m)
            elif _upcoming_signature(old) != (
                m.game_date, m.game_time, m.round, m.stadium, m.stadium_address
            ):
                conn.execute(
                    """UPDATE games_upcoming SET game_date=?, game_time=?, round=?, stadium=?,
                       stadium_address=? WHERE match_id=?""",
                    (m.game_date, m.game_time, m.round, m.stadium, m.stadium_address, m.match_id),
                )
                new_or_changed_games.append(m)

    conn.commit()
    return new_results, new_or_changed_games


# ---------------------------------------------------------------- выборки

def get_divisions(conn) -> list:
    return conn.execute("SELECT * FROM divisions ORDER BY tournament_id").fetchall()


def get_division(conn, tournament_id: int):
    return conn.execute(
        "SELECT * FROM divisions WHERE tournament_id=?", (tournament_id,)
    ).fetchone()


def get_teams_of_division(conn, tournament_id: int) -> list:
    return conn.execute(
        "SELECT * FROM teams WHERE division_id=? ORDER BY name", (tournament_id,)
    ).fetchall()


def get_team(conn, team_id: int, division_id: Optional[int] = None):
    if division_id is None:
        return conn.execute(
            "SELECT * FROM teams WHERE team_id=? ORDER BY division_id LIMIT 1", (team_id,)
        ).fetchone()
    return conn.execute(
        "SELECT * FROM teams WHERE team_id=? AND division_id=?", (team_id, division_id)
    ).fetchone()


def upcoming_by_division(conn, tournament_id: int, limit: int = 10, offset: int = 0) -> list:
    return conn.execute(
        """SELECT * FROM games_upcoming WHERE division_id=?
           ORDER BY game_date, game_time LIMIT ? OFFSET ?""",
        (tournament_id, limit, offset),
    ).fetchall()


def upcoming_count_division(conn, tournament_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM games_upcoming WHERE division_id=?", (tournament_id,)
    ).fetchone()["c"]


def upcoming_by_team(conn, team_id: int, limit: int = 10, offset: int = 0) -> list:
    return conn.execute(
        """SELECT * FROM games_upcoming WHERE home_team_id=? OR away_team_id=?
           ORDER BY game_date, game_time LIMIT ? OFFSET ?""",
        (team_id, team_id, limit, offset),
    ).fetchall()


def past_by_division(conn, tournament_id: int, limit: int = 10, offset: int = 0) -> list:
    return conn.execute(
        """SELECT * FROM games_past WHERE division_id=?
           ORDER BY game_date DESC, game_time DESC LIMIT ? OFFSET ?""",
        (tournament_id, limit, offset),
    ).fetchall()


def past_count_division(conn, tournament_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM games_past WHERE division_id=?", (tournament_id,)
    ).fetchone()["c"]


def past_by_team(conn, team_id: int, limit: int = 10, offset: int = 0) -> list:
    return conn.execute(
        """SELECT * FROM games_past WHERE home_team_id=? OR away_team_id=?
           ORDER BY game_date DESC, game_time DESC LIMIT ? OFFSET ?""",
        (team_id, team_id, limit, offset),
    ).fetchall()


# ---------------------------------------------------------------- подписки

def add_subscription(conn, user_id: int, chat_id: int, kind: str, ref_id: int, title: str) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO subscriptions(user_id, chat_id, kind, ref_id, title) VALUES(?,?,?,?,?)",
        (user_id, chat_id, kind, ref_id, title),
    )
    conn.commit()
    return cur.rowcount > 0


def remove_subscription(conn, user_id: int, kind: str, ref_id: int) -> None:
    conn.execute(
        "DELETE FROM subscriptions WHERE user_id=? AND kind=? AND ref_id=?",
        (user_id, kind, ref_id),
    )
    conn.commit()


def has_subscription(conn, user_id: int, kind: str, ref_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM subscriptions WHERE user_id=? AND kind=? AND ref_id=?",
        (user_id, kind, ref_id),
    ).fetchone() is not None


def user_subscriptions(conn, user_id: int) -> list:
    return conn.execute(
        "SELECT * FROM subscriptions WHERE user_id=? ORDER BY kind, title", (user_id,)
    ).fetchall()


def chat_subscriptions(conn, chat_id: int) -> list:
    return conn.execute(
        "SELECT * FROM subscriptions WHERE chat_id=? ORDER BY kind, title", (chat_id,)
    ).fetchall()


def match_touches_subscription(m: Match, kind: str, ref_id: int) -> bool:
    if kind == "division":
        return m.division_id == ref_id
    if kind == "team":
        return ref_id in (m.home_team_id, m.away_team_id)
    return False
