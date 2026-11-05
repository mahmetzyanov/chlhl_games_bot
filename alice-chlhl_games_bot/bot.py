# -*- coding: utf-8 -*-
"""Telegram-бот ЧЛХЛ (chlhl.ru).

Команды:
/start, /help  — меню и справка
/subs          — подписки
/refresh       — принудительное обновление данных

Данные обновляются при старте и по расписанию: 08:00, 13:00, 18:00, 23:00, 23:55.
Подписчики получают уведомления о новых играх и результатах.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import db
import parser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("chlhl-bot")

WD = {0: "пн", 1: "вт", 2: "ср", 3: "чт", 4: "пт", 5: "сб", 6: "вс"}

HELP_TEXT = (
    "🏒 <b>Бот ЧЛХЛ</b> — игры Челябинской любительской хоккейной лиги.\n\n"
    "<b>📅 Предстоящие игры</b> — расписание по дивизионам и командам.\n"
    "<b>🏁 Прошедшие игры</b> — результаты по дивизионам и командам.\n"
    "<b>🔔 Подписки</b> — подпишитесь на один или несколько дивизионов "
    "и/или команд; бот сообщит о новых играх и результатах.\n"
    "<b>♻️ Обновить данные</b> — получить свежие данные с сайта прямо сейчас.\n\n"
    "Данные также обновляются автоматически: при старте и в 08:00, 13:00, "
    "18:00, 23:00 и 23:55."
)


# ------------------------------------------------------------------ утилиты

def esc(s) -> str:
    return html.escape(str(s or ""))


async def safe_edit(message, text, reply_markup=None):
    """Правит сообщение, игнорируя ошибку Telegram 'message is not modified'."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


def fmt_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso or "?"
    return f"{d.day:02d}.{d.month:02d} ({WD[d.weekday()]})"


def fmt_upcoming(row) -> str:
    venue = esc(row["stadium"])
    addr = esc(row["stadium_address"])
    place = venue if not addr else f"{venue} ({addr})"
    return (
        f"🏒 {esc(row['home_team'])} — {esc(row['away_team'])}\n"
        f"📅 {fmt_date(row['game_date'])} {esc(row['game_time'])}"
        f"{', ' + esc(row['round']) if row['round'] else ''}\n"
        f"🏟 {place}"
    )


def fmt_past(row) -> str:
    venue = esc(row["stadium"])
    addr = esc(row["stadium_address"])
    place = venue if not addr else f"{venue} ({addr})"
    return (
        f"🏁 {esc(row['home_team'])} {row['home_score']}:{row['away_score']} {esc(row['away_team'])}\n"
        f"📅 {fmt_date(row['game_date'])} {esc(row['game_time'])}"
        f"{', ' + esc(row['round']) if row['round'] else ''}\n"
        f"🏟 {place}"
    )


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def main_menu() -> InlineKeyboardMarkup:
    return kb(
        [btn("📅 Предстоящие игры", "up"), btn("🏁 Прошедшие игры", "past")],
        [btn("🔔 Подписки", "subs")],
        [btn("♻️ Обновить данные", "refresh"), btn("ℹ️ Справка", "help")],
        [btn("🏠 Главное меню", "menu")],
    )


# ------------------------------------------------------------------ обновление данных

def _fmt_event(kind: str, m: parser.Match) -> str:
    div = m.round and f", {m.round}" or ""
    place = m.stadium + (f" ({m.stadium_address})" if m.stadium_address else "")
    if kind == "result":
        return (
            f"🏁 <b>{esc(m.home_name)} {m.home_score}:{m.away_score} {esc(m.away_name)}</b>\n"
            f"📅 {fmt_date(m.game_date)} {esc(m.game_time)}{div}\n🏟 {esc(place)}"
        )
    return (
        f"🔔 <b>Новая игра:</b> {esc(m.home_name)} — {esc(m.away_name)}\n"
        f"📅 {fmt_date(m.game_date)} {esc(m.game_time)}{div}\n🏟 {esc(place)}"
    )


async def run_update(bot: Bot | None) -> str:
    """Полное обновление данных и рассылка подписчикам. Возвращает отчёт."""
    divisions, teams, matches, stadiums = await asyncio.to_thread(parser.fetch_all)

    conn = db.connect(config.DB_PATH)
    db.save_divisions(conn, divisions)
    db.save_teams(conn, teams)
    db.save_stadiums(conn, stadiums)
    new_results, new_games = db.sync_matches(conn, matches)

    events = [("result", m) for m in new_results] + [("new", m) for m in new_games]

    if bot and events:
        chats = {r["chat_id"] for r in conn.execute("SELECT DISTINCT chat_id FROM subscriptions")}
        for chat_id in chats:
            subs_rows = db.chat_subscriptions(conn, chat_id)
            lines: list[str] = []
            for kind, m in events:
                for s in subs_rows:
                    if db.match_touches_subscription(m, s["kind"], s["ref_id"]):
                        lines.append(_fmt_event(kind, m))
                        break
            if lines:
                for i in range(0, len(lines), 15):
                    try:
                        await bot.send_message(chat_id, "\n\n".join(lines[i : i + 15]))
                    except Exception:
                        log.exception("Не удалось отправить уведомление в %s", chat_id)
                    await asyncio.sleep(0.4)

    return (
        f"Дивизионов: {len(divisions)}, команд: {len(teams)}, матчей: {len(matches)}.\n"
        f"Новых результатов: {len(new_results)}, новых/изменённых игр: {len(new_games)}."
    )


async def scheduler_loop(bot: Bot) -> None:
    fired: set = set()
    while True:
        now = datetime.now()
        for h, mnt in config.UPDATE_TIMES:
            key = (now.date(), h, mnt)
            if now.hour == h and now.minute == mnt and key not in fired:
                fired.add(key)
                # Чистим старые отметки
                fired = {k for k in fired if k[0] >= now.date() - timedelta(days=1)}
                log.info("Плановое обновление %02d:%02d", h, mnt)
                try:
                    await run_update(bot)
                except Exception:
                    log.exception("Ошибка планового обновления")
        await asyncio.sleep(15)


# ------------------------------------------------------------------ хендлеры

def make_dispatcher() -> Dispatcher:
    dp = Dispatcher()

    @dp.message(CommandStart())
    @dp.message(Command("help"))
    async def start(message: Message):
        await message.answer(HELP_TEXT, reply_markup=main_menu())

    @dp.message(Command("refresh"))
    async def refresh_cmd(message: Message):
        msg = await message.answer("♻️ Обновляю данные…")
        report = await run_update(message.bot)
        await msg.edit_text(f"✅ Данные обновлены.\n{report}", reply_markup=main_menu())

    @dp.message(Command("subs"))
    async def subs_cmd(message: Message):
        await message.answer("🔔 Ваши подписки", reply_markup=subs_menu_kb())

    @dp.callback_query(F.data == "menu")
    async def cb_menu(cb: CallbackQuery):
        await safe_edit(cb.message, HELP_TEXT, reply_markup=main_menu())
        await cb.answer()

    @dp.callback_query(F.data == "help")
    async def cb_help(cb: CallbackQuery):
        await safe_edit(cb.message, HELP_TEXT, reply_markup=main_menu())
        await cb.answer()

    @dp.callback_query(F.data == "refresh")
    async def cb_refresh(cb: CallbackQuery):
        await cb.answer("Обновляю данные…")
        report = await run_update(cb.bot)
        await cb.message.answer(f"✅ Данные обновлены.\n{report}", reply_markup=main_menu())

    # ------------------------------------------------ предстоящие / прошедшие

    @dp.callback_query(F.data.in_({"up", "past"}))
    async def cb_browse(cb: CallbackQuery):
        mode = cb.data
        await safe_edit(cb.message,
            "Выберите дивизион:",
            reply_markup=divisions_kb(mode),
        )
        await cb.answer()

    @dp.callback_query(F.data.regexp(r"^(updiv|pastdiv):(\d+)(?::(\d+))?$"))
    async def cb_division_games(cb: CallbackQuery):
        parts = _parts(cb.data)
        mode, tid = parts[0], int(parts[1])
        off = int(parts[2]) if len(parts) > 2 else 0
        await show_division_games(cb, mode, tid, off)

    @dp.callback_query(F.data.regexp(r"^(upteams|pastteams):(\d+)$"))
    async def cb_division_teams(cb: CallbackQuery):
        mode, tid, _ = _parts(cb.data)
        await show_division_teams(cb, mode, int(tid))

    @dp.callback_query(F.data.regexp(r"^(upteam|pastteam):(\d+):(\d+)(?::(\d+))?$"))
    async def cb_team_games(cb: CallbackQuery):
        parts = _parts(cb.data)
        mode, tid, team_id = parts[0], int(parts[1]), int(parts[2])
        off = int(parts[3]) if len(parts) > 3 else 0
        await show_team_games(cb, mode, tid, team_id, off)

    # ------------------------------------------------------------- подписки

    @dp.callback_query(F.data == "subs")
    async def cb_subs(cb: CallbackQuery):
        await safe_edit(cb.message, "🔔 Ваши подписки", reply_markup=subs_menu_kb())
        await cb.answer()

    @dp.callback_query(F.data.in_({"subd", "subt"}))
    async def cb_sub_kind(cb: CallbackQuery):
        if cb.data == "subd":
            await safe_edit(cb.message,
                "Выберите дивизионы для подписки (можно несколько):",
                reply_markup=sub_divisions_kb(cb.from_user.id),
            )
        else:
            await safe_edit(cb.message,
                "Сначала выберите дивизион команды:",
                reply_markup=sub_divisions_kb(cb.from_user.id, for_teams=True),
            )
        await cb.answer()

    @dp.callback_query(F.data.regexp(r"^subd:(\d+)$"))
    async def cb_sub_division(cb: CallbackQuery):
        tid = int(cb.data.split(":")[1])
        conn = db.connect(config.DB_PATH)
        div = db.get_division(conn, tid)
        if div:
            if db.has_subscription(conn, cb.from_user.id, "division", tid):
                db.remove_subscription(conn, cb.from_user.id, "division", tid)
            else:
                db.add_subscription(conn, cb.from_user.id, cb.message.chat.id, "division", tid, div["name"])
        await cb.message.edit_reply_markup(
            reply_markup=sub_divisions_kb(cb.from_user.id)
        )
        await cb.answer()

    @dp.callback_query(F.data.regexp(r"^subtd:(\d+)$"))
    async def cb_sub_teams_list(cb: CallbackQuery):
        tid = int(cb.data.split(":")[1])
        await show_sub_teams(cb, tid)

    @dp.callback_query(F.data.regexp(r"^subt:(\d+):(\d+)$"))
    async def cb_sub_team(cb: CallbackQuery):
        _, tid, team_id = cb.data.split(":")
        conn = db.connect(config.DB_PATH)
        team = db.get_team(conn, int(team_id), int(tid))
        if team:
            title = f"{team['name']} ({db.get_division(conn, int(tid))['name']})"
            if db.has_subscription(conn, cb.from_user.id, "team", int(team_id)):
                db.remove_subscription(conn, cb.from_user.id, "team", int(team_id))
            else:
                db.add_subscription(
                    conn, cb.from_user.id, cb.message.chat.id, "team", int(team_id), title
                )
        await show_sub_teams(cb, int(tid))
        await cb.answer()

    @dp.callback_query(F.data == "submy")
    async def cb_sub_my(cb: CallbackQuery):
        conn = db.connect(config.DB_PATH)
        rows = db.user_subscriptions(conn, cb.from_user.id)
        if not rows:
            await safe_edit(cb.message,
                "У вас пока нет подписок.", reply_markup=subs_menu_kb()
            )
        else:
            rows_kb = [
                [btn(f"🗑 {r['title']}", f"unsub:{r['kind']}:{r['ref_id']}")] for r in rows
            ]
            rows_kb.append([btn("◀️ Назад", "subs")])
            await safe_edit(cb.message,
                "Ваши подписки (нажмите, чтобы отписаться):",
                reply_markup=kb(*rows_kb),
            )
        await cb.answer()

    @dp.callback_query(F.data.regexp(r"^unsub:(division|team):(\d+)$"))
    async def cb_unsub(cb: CallbackQuery):
        _, kind, ref = cb.data.split(":")
        db.remove_subscription(db.connect(config.DB_PATH), cb.from_user.id, kind, int(ref))
        await cb_sub_my(cb)

    return dp


def _parts(data: str) -> list:
    return data.split(":")


# ------------------------------------------------------------------ экраны

def divisions_kb(mode: str) -> InlineKeyboardMarkup:
    conn = db.connect(config.DB_PATH)
    rows = []
    for d in db.get_divisions(conn):
        rows.append([btn(d["name"], f"{mode}div:{d['tournament_id']}")])
    rows.append([btn("🏠 Главное меню", "menu")])
    return kb(*rows)


async def show_division_games(cb: CallbackQuery, mode: str, tid: int, offset: int):
    conn = db.connect(config.DB_PATH)
    div = db.get_division(conn, tid)
    if not div:
        await cb.answer("Дивизион не найден, обновите данные", show_alert=True)
        return
    total = (
        db.upcoming_count_division(conn, tid)
        if mode == "up"
        else db.past_count_division(conn, tid)
    )
    rows = (
        db.upcoming_by_division(conn, tid, config.GAMES_PER_PAGE, offset)
        if mode == "up"
        else db.past_by_division(conn, tid, config.GAMES_PER_PAGE, offset)
    )
    if not rows:
        text = f"<b>{esc(div['name'])}</b>\n\nИгр пока нет."
        nav = []
    else:
        title = "📅 Предстоящие игры" if mode == "up" else "🏁 Результаты"
        text = (
            f"<b>{title}: {esc(div['name'])}</b> ({total} всего)\n\n"
            + "\n\n".join(fmt_upcoming(r) if mode == "up" else fmt_past(r) for r in rows)
        )
        nav = []
    if offset > 0:
        nav.append(btn("⬅️ Назад", f"{mode}div:{tid}:{offset - config.GAMES_PER_PAGE}"))
    if offset + config.GAMES_PER_PAGE < total:
        nav.append(btn("Вперёд ➡️", f"{mode}div:{tid}:{offset + config.GAMES_PER_PAGE}"))
    if mode == "up":
        nav.append(btn("👥 По командам", f"{mode}teams:{tid}"))
    elif not nav:
        nav = [btn("👥 Другая команда", f"{mode}teams:{tid}")]

    keyboard = kb(
        nav or [btn("👥 По командам", f"{mode}teams:{tid}")],
        [btn("◀️ Другой дивизион", mode)],
        [btn("🏠 Главное меню", "menu")],
    )
    await safe_edit(cb.message, text, reply_markup=keyboard)
    await cb.answer()


async def show_division_teams(cb: CallbackQuery, mode: str, tid: int):
    conn = db.connect(config.DB_PATH)
    div = db.get_division(conn, tid)
    teams = db.get_teams_of_division(conn, tid)
    rows = [[btn(t["name"], f"{mode}team:{tid}:{t['team_id']}")] for t in teams]
    rows.append([btn("◀️ К играм дивизиона", f"{mode}div:{tid}")])
    rows.append([btn("◀️ Другой дивизион", mode)])
    await safe_edit(cb.message,
        f"Команды дивизиона «{esc(div['name'] if div else '')}»: выберите команду",
        reply_markup=kb(*rows),
    )
    await cb.answer()


async def show_team_games(cb: CallbackQuery, mode: str, tid: int, team_id: int, offset: int):
    conn = db.connect(config.DB_PATH)
    div = db.get_division(conn, tid)
    team = db.get_team(conn, team_id, tid)
    if not team:
        await cb.answer("Команда не найдена, обновите данные", show_alert=True)
        return
    rows = (
        db.upcoming_by_team(conn, team_id, config.GAMES_PER_PAGE, offset)
        if mode == "up"
        else db.past_by_team(conn, team_id, config.GAMES_PER_PAGE, offset)
    )
    # Оставляем только игры текущего дивизиона, чтобы не путать одноимённые клубы
    rows = [r for r in rows if r["division_id"] == tid]

    label = "📅 Предстоящие игры" if mode == "up" else "🏁 Результаты"
    if not rows:
        text = f"<b>{esc(team['name'])} ({esc(div['name'] if div else '')})</b>\n\nИгр пока нет."
        nav = []
    else:
        text = (
            f"<b>{label}: {esc(team['name'])}</b>\n\n"
            + "\n\n".join(fmt_upcoming(r) if mode == "up" else fmt_past(r) for r in rows)
        )
        nav = []
        if offset > 0:
            nav.append(btn("⬅️ Назад", f"{mode}team:{tid}:{team_id}:{offset - config.GAMES_PER_PAGE}"))
        if len(rows) == config.GAMES_PER_PAGE:
            nav.append(btn("Вперёд ➡️", f"{mode}team:{tid}:{team_id}:{offset + config.GAMES_PER_PAGE}"))

    keyboard = kb(
        nav or [btn("👥 Другая команда", f"{mode}teams:{tid}")],
        [btn("◀️ Команды дивизиона", f"{mode}teams:{tid}"),
         btn("◀️ Другой дивизион", mode)],
        [btn("🏠 Главное меню", "menu")],
    )
    await safe_edit(cb.message, text, reply_markup=keyboard)
    await cb.answer()


def subs_menu_kb() -> InlineKeyboardMarkup:
    return kb(
        [btn("➕ Дивизионы", "subd"), btn("➕ Команды", "subt")],
        [btn("📋 Мои подписки", "submy")],
        [btn("🏠 Главное меню", "menu")],
    )


def sub_divisions_kb(user_id: int, for_teams: bool = False) -> InlineKeyboardMarkup:
    conn = db.connect(config.DB_PATH)
    prefix = "subtd" if for_teams else "subd"
    rows = []
    for d in db.get_divisions(conn):
        if for_teams:
            label = d["name"]
        else:
            subscribed = db.has_subscription(conn, user_id, "division", d["tournament_id"])
            label = ("✅ " if subscribed else "➕ ") + d["name"]
        rows.append([btn(label, f"{prefix}:{d['tournament_id']}")])
    if not for_teams:
        rows.append([btn("✅ Готово", "subs")])
    rows.append([btn("🏠 Главное меню", "menu")])
    return kb(*rows)


async def show_sub_teams(cb: CallbackQuery, tid: int):
    conn = db.connect(config.DB_PATH)
    div = db.get_division(conn, tid)
    teams = db.get_teams_of_division(conn, tid)
    rows = []
    for t in teams:
        subscribed = db.has_subscription(conn, cb.from_user.id, "team", t["team_id"])
        label = ("✅ " if subscribed else "➕ ") + t["name"]
        rows.append([btn(label, f"subt:{tid}:{t['team_id']}")])
    rows.append([btn("◀️ Другой дивизион", "subt")])
    rows.append([btn("✅ Готово", "subs"), btn("📋 Мои подписки", "submy")])
    await safe_edit(cb.message,
        f"Команды дивизиона «{esc(div['name'] if div else '')}» — выберите для подписки (можно несколько):",
        reply_markup=kb(*rows),
    )
    await cb.answer()


# ------------------------------------------------------------------ запуск

async def main():
    if not config.BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN не задан. Создайте файл .env рядом с bot.py со строкой:\n"
            "    BOT_TOKEN=123456:ABC-DEF..."
        )

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = make_dispatcher()

    log.info("Первичное обновление данных…")
    try:
        report = await run_update(bot)
        log.info("Обновление завершено: %s", report)
    except Exception:
        log.exception("Первичное обновление не удалось — бот продолжит работу с имеющейся БД")

    asyncio.create_task(scheduler_loop(bot))

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен")
