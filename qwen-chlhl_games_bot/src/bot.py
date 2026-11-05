import logging
import asyncio
import os
import aiosqlite
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import CallbackQuery
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

##import database as db
##from scraper import run_scraper
#from database import init_db, save_divisions, save_teams, save_matches, DB_NAME
#from scraper import run_scraper
import database as db
import scraper

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
logging.basicConfig(level=logging.INFO)

async def update_data(notify_users=False):
    logging.info("Запуск обновления данных...")
    try:
        divisions, teams, matches = await run_scraper()
        div_map = await db.save_divisions(divisions)
        team_map = await db.save_teams(teams, div_map)
        new_matches = await db.save_matches(matches, div_map, team_map)
        
        if notify_users and new_matches:
            await notify_new_matches(new_matches)
    except Exception as e:
        logging.error(f"Ошибка обновления: {e}")

async def notify_new_matches(new_matches):
    async with aiosqlite.connect(db.DB_NAME) as conn:
        conn.row_factory = aiosqlite.Row
        for m in new_matches:
            if m.get('is_played'): continue
            
            cursor = await conn.execute("SELECT * FROM matches WHERE chlhl_id = ?", (m['chlhl_id'],))
            db_match = await cursor.fetchone()
            if not db_match: continue
            
            div_id = db_match['division_id']
            t1_id, t2_id = db_match['team1_id'], db_match['team2_id']
            
            query = "SELECT user_id FROM subscriptions WHERE division_id = ?"
            params = [div_id]
            if t1_id: query += " OR team_id = ?"; params.append(t1_id)
            if t2_id: query += " OR team_id = ?"; params.append(t2_id)
                
            sub_cur = await conn.execute(query, params)
            subscribers = await sub_cur.fetchall()
            
            if subscribers:
                text = (f"🔔 <b>Новая игра добавлена!</b>\n\n"
                        f"🏒 {m['team1_name']} - {m['team2_name']}\n"
                        f"🗓 {m['date']} ⏰ {m['time']}\n"
                        f"🏟 {m.get('stadium', 'Уточняется')} | 📍 {m.get('address', '')}\n")
                user_ids = set(s['user_id'] for s in subscribers)
                for uid in user_ids:
                    try:
                        await bot.send_message(uid, text, parse_mode="HTML")
                        await asyncio.sleep(0.1) # Защита от лимитов Telegram
                    except Exception as e:
                        logging.error(f"Ошибка отправки {uid}: {e}")

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Будущие игры", callback_data="menu_future")
    kb.button(text="🏁 Прошедшие игры", callback_data="menu_past")
    kb.button(text="🔔 Подписки", callback_data="menu_subs")
    kb.adjust(1)
    await message.answer("🏒 Привет! Я бот ЧЛХЛ.\nВыберите раздел:", reply_markup=kb.as_markup())

@router.callback_query(F.data == "menu_future")
async def menu_future(c: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="🏆 По дивизионам", callback_data="future_by_div")
    kb.button(text="🏒 По командам", callback_data="future_by_team")
    kb.adjust(1)
    await c.message.edit_text("📅 Как отфильтровать будущие игры?", reply_markup=kb.as_markup())

@router.callback_query(F.data == "menu_past")
async def menu_past(c: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="🏆 По дивизионам", callback_data="past_by_div")
    kb.button(text="🏒 По командам", callback_data="past_by_team")
    kb.adjust(1)
    await c.message.edit_text("🏁 Как отфильтровать прошедшие игры?", reply_markup=kb.as_markup())

@router.callback_query(F.data == "menu_subs")
async def menu_subs(c: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ На дивизион", callback_data="subs_div")
    kb.button(text="➕ На команду", callback_data="subs_team")
    kb.button(text="📋 Мои подписки", callback_data="subs_list")
    kb.adjust(1)
    await c.message.edit_text("🔔 Настройка уведомлений:", reply_markup=kb.as_markup())

@router.callback_query(F.data.in_(["future_by_div", "past_by_div", "subs_div"]))
async def select_division(c: CallbackQuery):
    divisions = await db.get_all_divisions()
    kb = InlineKeyboardBuilder()
    action = c.data.split('_')[-1]
    prefix = "future_div" if action == "future" else ("past_div" if action == "past" else "toggle_div")
    for d in divisions: kb.button(text=d['name'], callback_data=f"{prefix}_{d['id']}")
    kb.adjust(1)
    kb.button(text="⬅️ Назад", callback_data=f"menu_{action}" if action in ["future", "past"] else "menu_subs")
    await c.message.edit_text("Выберите дивизион:", reply_markup=kb.as_markup())

@router.callback_query(F.data.in_(["future_by_team", "past_by_team", "subs_team"]))
async def select_team_start(c: CallbackQuery):
    divisions = await db.get_all_divisions()
    kb = InlineKeyboardBuilder()
    action = c.data.split('_')[-1]
    prefix = "future_team_div" if action == "future" else ("past_team_div" if action == "past" else "toggle_team_div")
    for d in divisions: kb.button(text=d['name'], callback_data=f"{prefix}_{d['id']}")
    kb.adjust(1)
    kb.button(text="⬅️ Назад", callback_data=f"menu_{action}" if action in ["future", "past"] else "menu_subs")
    await c.message.edit_text("Выберите дивизион, чтобы увидеть команды:", reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith("future_div_"))
async def show_future_by_div(c: CallbackQuery):
    div_id = int(c.data.split('_')[-1])
    matches = await db.get_upcoming_matches_by_div(div_id)
    text = "📅 <b>Предстоящие игры:</b>\n\n"
    if not matches: text = "Нет предстоящих игр."
    for m in matches:
        text += f"🏒 {m['team1_name']} - {m['team2_name']}\n🗓 {m['date']} ⏰ {m['time']}\n🏟 {m['stadium']} | 📍 {m['address']}\n\n"
    await c.message.edit_text(text, parse_mode="HTML")

@router.callback_query(F.data.startswith("past_div_"))
async def show_past_by_div(c: CallbackQuery):
    div_id = int(c.data.split('_')[-1])
    matches = await db.get_past_matches_by_div(div_id)
    text = "🏁 <b>Прошедшие игры:</b>\n\n"
    if not matches: text = "Нет прошедших игр."
    for m in matches:
        text += f"🏒 {m['team1_name']} - {m['team2_name']}\n🏆 <b>{m['score1']} : {m['score2']}</b>\n🗓 {m['date']} | 🏟 {m['stadium']}\n\n"
    await c.message.edit_text(text, parse_mode="HTML")

@router.callback_query(F.data.startswith("future_team_div_"))
async def select_team_for_future(c: CallbackQuery):
    div_id = int(c.data.split('_')[-1])
    teams = await db.get_teams_by_division(div_id)
    kb = InlineKeyboardBuilder()
    for t in teams: kb.button(text=t['name'], callback_data=f"future_team_{t['id']}")
    kb.adjust(1)
    kb.button(text="⬅️ Назад", callback_data="future_by_team")
    await c.message.edit_text("Выберите команду:", reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith("future_team_"))
async def show_future_by_team(c: CallbackQuery):
    team_id = int(c.data.split('_')[-1])
    matches = await db.get_upcoming_matches_by_team(team_id)
    text = "📅 <b>Предстоящие игры команды:</b>\n\n"
    if not matches: text = "Нет предстоящих игр."
    for m in matches:
        text += f"🏒 {m['team1_name']} - {m['team2_name']}\n🗓 {m['date']} ⏰ {m['time']}\n🏟 {m['stadium']} | 📍 {m['address']}\n\n"
    await c.message.edit_text(text, parse_mode="HTML")

@router.callback_query(F.data.startswith("past_team_div_"))
async def select_team_for_past(c: CallbackQuery):
    div_id = int(c.data.split('_')[-1])
    teams = await db.get_teams_by_division(div_id)
    kb = InlineKeyboardBuilder()
    for t in teams: kb.button(text=t['name'], callback_data=f"past_team_{t['id']}")
    kb.adjust(1)
    kb.button(text="⬅️ Назад", callback_data="past_by_team")
    await c.message.edit_text("Выберите команду:", reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith("past_team_"))
async def show_past_by_team(c: CallbackQuery):
    team_id = int(c.data.split('_')[-1])
    matches = await db.get_past_matches_by_team(team_id)
    text = "🏁 <b>Прошедшие игры команды:</b>\n\n"
    if not matches: text = "Нет прошедших игр."
    for m in matches:
        text += f"🏒 {m['team1_name']} - {m['team2_name']}\n🏆 <b>{m['score1']} : {m['score2']}</b>\n🗓 {m['date']} | 🏟 {m['stadium']}\n\n"
    await c.message.edit_text(text, parse_mode="HTML")

@router.callback_query(F.data.startswith("toggle_div_"))
async def toggle_div_sub(c: CallbackQuery):
    div_id = int(c.data.split('_')[-1])
    is_subbed = await db.toggle_subscription(c.from_user.id, division_id=div_id)
    divs = await db.get_all_divisions()
    div_name = next((d['name'] for d in divs if d['id'] == div_id), "этот дивизион")
    await c.answer(f"{'✅ Подписка на ' + div_name if is_subbed else '❌ Отписка от ' + div_name}", show_alert=True)

@router.callback_query(F.data.startswith("toggle_team_div_"))
async def select_team_for_sub(c: CallbackQuery):
    div_id = int(c.data.split('_')[-1])
    teams = await db.get_teams_by_division(div_id)
    kb = InlineKeyboardBuilder()
    for t in teams: kb.button(text=t['name'], callback_data=f"toggle_team_{t['id']}")
    kb.adjust(1)
    kb.button(text="⬅️ Назад", callback_data="subs_team")
    await c.message.edit_text("Выберите команду для подписки:", reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith("toggle_team_"))
async def toggle_team_sub(c: CallbackQuery):
    team_id = int(c.data.split('_')[-1])
    is_subbed = await db.toggle_subscription(c.from_user.id, team_id=team_id)
    await c.answer(f"{'✅ Подписка оформлена' if is_subbed else '❌ Подписка отменена'}", show_alert=True)

@router.callback_query(F.data == "subs_list")
async def show_subs_list(c: CallbackQuery):
    subs = await db.get_user_subscriptions(c.from_user.id)
    if not subs:
        text = "У вас пока нет активных подписок."
        kb = InlineKeyboardBuilder()
        kb.button(text="⬅️ Назад", callback_data="menu_subs")
        await c.message.edit_text(text, reply_markup=kb.as_markup())
    else:
        text = "📋 <b>Ваши подписки:</b>\n<i>Нажмите, чтобы отписаться</i>\n\n"
        kb = InlineKeyboardBuilder()
        for s in subs:
            if s['div_name']:
                text += f"🏆 Дивизион: <b>{s['div_name']}</b>\n"
                kb.button(text=f"❌ {s['div_name']}", callback_data=f"toggle_div_{s['div_id']}")
            if s['team_name']:
                text += f"🏒 Команда: <b>{s['team_name']}</b>\n"
                kb.button(text=f"❌ {s['team_name']}", callback_data=f"toggle_team_{s['team_id']}")
        kb.adjust(1)
        kb.button(text="⬅️ Назад", callback_data="menu_subs")
        await c.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")

async def main():
    await db.init_db()
    await update_data(notify_users=False) # Первичное наполнение БД без спама
    
    # Расписание обновлений (Челябинск = Asia/Yekaterinburg)
    scheduler = AsyncIOScheduler(timezone="Asia/Yekaterinburg")
    scheduler.add_job(update_data, 'cron', hour=8, minute=0, args=(True,))
    scheduler.add_job(update_data, 'cron', hour=13, minute=0, args=(True,))
    scheduler.add_job(update_data, 'cron', hour=18, minute=0, args=(True,))
    scheduler.add_job(update_data, 'cron', hour=23, minute=0, args=(True,))
    scheduler.add_job(update_data, 'cron', hour=23, minute=55, args=(True,))
    scheduler.start()
    
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен")