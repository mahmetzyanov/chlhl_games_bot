# -*- coding: utf-8 -*-
"""Парсер сайта Челябинской любительской хоккейной лиги (chlhl.ru).

Отдаёт:
- get_divisions()      — список дивизионов (турниров)
- get_teams(tid)       — команды дивизиона
- get_matches(tid)     — матчи календаря дивизиона (сыгранные и будущие)
- get_stadiums()       — словарь «стадион -> адрес»
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://chlhl.ru"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "маи": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}

RE_DIVISION = re.compile(r'href="/tournament/(\d+)"[^>]*>([^<]+)</a>')
RE_TEAM_ITEM = re.compile(r'<li class="blk-tteams__item".*?>(.*?)</li>', re.S)
RE_TEAM_ID = re.compile(r"team_id=(\d+)")
RE_TEAM_NAME = re.compile(r'<div class="blk-tteams__name">([^<]+)</div>')
RE_TEAM_CITY = re.compile(r'<div class="blk-tteams__region">([^<]+)</div>')
RE_DATE_HEAD = re.compile(r'<span class="blk-tcalendar__head-text">([^<]+)</span>')
RE_MATCH_START = re.compile(r'<li class="blk-tcalendar__badges-item([^"]*)"')
RE_MATCH_ID = re.compile(r'href="/match/(\d+)"')
RE_TEAM_NAME_LINKS = re.compile(r'<div class="blk-tcalendar__badges-team-name">([^<]+)</div>')
RE_TEAM_IDS = re.compile(r"teams/application\?team_id=(\d+)")
RE_SCORE = re.compile(r'<div class="blk-tcalendar__badges-score-main">([^<]*)</div>')
RE_TIME_BADGE = re.compile(r'<div class="blk-tcalendar__badges-time">([^<]+)</div>')
RE_INFO_TITLE = re.compile(r'<div class="blk-tcalendar__badges-info-text" title="([^"]*)"')
RE_STADIUM_BLOCK = re.compile(r'<li class="blk-stadiums__item.*?(?=<li class="blk-stadiums__item|$)', re.S)
RE_STADIUM_NAME = re.compile(r'class="blk-stadiums__title">([^<]+)</a>')
RE_STADIUM_ADDRESS = re.compile(r'data-stadium-address="([^"]*)"')


@dataclass
class Division:
    tournament_id: int
    name: str


@dataclass
class Team:
    team_id: int
    division_id: int
    name: str
    city: str


@dataclass
class Match:
    match_id: int
    division_id: int
    game_date: str                 # "YYYY-MM-DD"
    game_time: str                 # "HH:MM" или ""
    round: str                     # "4 тур"
    home_team_id: Optional[int]
    home_name: str
    away_team_id: Optional[int]
    away_name: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    stadium: str = ""
    stadium_address: str = ""
    is_played: bool = False


def _get(session: requests.Session, path: str) -> str:
    url = BASE_URL + path
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def _clean(s: str) -> str:
    return html.unescape(s or "").strip()


def _parse_date(text: str, today: Optional[date] = None) -> Optional[str]:
    """'7 ноября, суббота' -> '2026-11-07'. Год угадываем по близости к сегодняшней дате."""
    today = today or date.today()
    m = re.match(r"(\d{1,2})\s+([а-яё]+)", text.strip().lower())
    if not m:
        return None
    day, mon_name = int(m.group(1)), m.group(2)[:3]
    month = MONTHS.get(mon_name)
    if not month:
        return None
    for year in (today.year, today.year - 1, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if abs((d - today).days) <= 200:
            return d.isoformat()
    return None


def get_divisions(session: requests.Session) -> list[Division]:
    text = _get(session, "/tournaments")
    out, seen = [], set()
    for tid, name in RE_DIVISION.findall(text):
        tid = int(tid)
        name = _clean(name)
        if tid in seen:
            continue
        seen.add(tid)
        out.append(Division(tid, name))
    return out


def get_teams(session: requests.Session, tournament_id: int) -> list[Team]:
    text = _get(session, f"/tournament/{tournament_id}/teams")
    out = []
    for block in RE_TEAM_ITEM.findall(text):
        ids = RE_TEAM_ID.findall(block)
        names = RE_TEAM_NAME.findall(block)
        cities = RE_TEAM_CITY.findall(block)
        if not ids or not names:
            continue
        out.append(
            Team(
                team_id=int(ids[0]),
                division_id=tournament_id,
                name=_clean(names[0]),
                city=_clean(cities[0]) if cities else "",
            )
        )
    return out


def _year_for(d: date, today: date) -> int:
    """Выбираем год так, чтобы дата была в пределах +-200 дней от сегодня."""
    for year in (today.year, today.year + 1, today.year - 1):
        try:
            cand = date(year, d.month, d.day)
        except ValueError:
            continue
        if abs((cand - today).days) <= 200:
            return year
    return today.year


def get_matches(session: requests.Session, tournament_id: int) -> list[Match]:
    text = _get(session, f"/tournament/{tournament_id}/calendar")
    today = date.today()

    # Разбиваем страницу на отрезки по заголовкам дат
    heads = list(RE_DATE_HEAD.finditer(text))
    matches: list[Match] = []
    for i, head in enumerate(heads):
        start = head.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        seg = text[start:end]
        d_str = _parse_date(head.group(1), today)
        starts = list(RE_MATCH_START.finditer(seg))
        for i, ms_ in enumerate(starts):
            flags = ms_.group(1)
            body = seg[ms_.end(): starts[i + 1].start() if i + 1 < len(starts) else len(seg)]
            mid = RE_MATCH_ID.search(body)
            names = RE_TEAM_NAME_LINKS.findall(body)
            ids = RE_TEAM_IDS.findall(body)
            if not mid or len(names) < 2 or len(ids) < 2:
                continue
            infos = RE_INFO_TITLE.findall(body)
            round_name, time_str = "", ""
            if infos:
                parts = infos[0].split(",", 1)
                round_name = _clean(parts[0])
                time_str = _clean(parts[1]) if len(parts) > 1 else ""
            stadium = _clean(infos[1]) if len(infos) > 1 else ""

            score_txt = _clean(RE_SCORE.search(body).group(1)) if RE_SCORE.search(body) else ""
            home_score = away_score = None
            sm = re.match(r"(\d+)\s*[-–:]\s*(\d+)", score_txt)
            if sm:
                home_score, away_score = int(sm.group(1)), int(sm.group(2))
            else:
                tm = RE_TIME_BADGE.search(body)
                if tm and not time_str:
                    time_str = re.sub(r"\s+", "", tm.group(1))

            is_played = "js-tcal-played" in flags or home_score is not None

            matches.append(
                Match(
                    match_id=int(mid.group(1)),
                    division_id=tournament_id,
                    game_date=d_str or "",
                    game_time=time_str,
                    round=round_name,
                    home_team_id=int(ids[0]) or None,
                    home_name=_clean(names[0]),
                    away_team_id=int(ids[1]) or None,
                    away_name=_clean(names[1]),
                    home_score=home_score,
                    away_score=away_score,
                    stadium=stadium,
                    is_played=is_played,
                )
            )
    return matches


def get_stadiums(session: requests.Session) -> dict[str, str]:
    text = _get(session, "/stadiums")
    out: dict[str, str] = {}
    for block in RE_STADIUM_BLOCK.findall(text):
        nm = RE_STADIUM_NAME.search(block)
        am = RE_STADIUM_ADDRESS.search(block)
        if nm and am:
            name = _clean(nm.group(1))
            out[name] = _clean(am.group(1))
    return out


def enrich_address(matches: list[Match], stadiums: dict[str, str]) -> None:
    """Проставляет адрес стадиона по его названию (нормализуем пробелы)."""
    norm = {re.sub(r"\s+", " ", k).strip().lower(): v for k, v in stadiums.items()}
    for m in matches:
        key = re.sub(r"\s+", " ", m.stadium).strip().lower()
        m.stadium_address = norm.get(key, "")


def fetch_all() -> tuple[list[Division], list[Team], list[Match], dict[str, str]]:
    """Полный обход сайта. Возвращает (дивизионы, команды, все матчи, стадионы)."""
    s = requests.Session()
    s.headers.update(HEADERS)

    divisions = get_divisions(s)
    teams: list[Team] = []
    matches: list[Match] = []
    for d in divisions:
        try:
            teams.extend(get_teams(s, d.tournament_id))
        except Exception:
            log.exception("Не удалось получить команды дивизиона %s", d.name)
        try:
            matches.extend(get_matches(s, d.tournament_id))
        except Exception:
            log.exception("Не удалось получить календарь дивизиона %s", d.name)
        # Вежливо к сайту: небольшая пауза между запросами
        import time
        time.sleep(0.5)

    stadiums = get_stadiums(s)
    enrich_address(matches, stadiums)
    return divisions, teams, matches, stadiums


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    divs, tms, mts, std = fetch_all()
    print("Дивизионы:", [d.name for d in divs])
    print("Команд всего:", len(tms), "| Матчей всего:", len(mts))
    played = [m for m in mts if m.is_played]
    future = [m for m in mts if not m.is_played]
    print("Сыграно:", len(played), "| Будущих:", len(future))
    if mts:
        m = mts[0]
        print("Пример матча:", m)
