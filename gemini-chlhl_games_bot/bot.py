import os
import asyncio
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database as db
import parser

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# Главное меню
def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Предстоящие игры", callback_data="nav_upcoming")],
        [InlineKeyboardButton(text="🏆 Прошедшие игры", callback_data="nav_past")],
        [InlineKeyboardButton(text="🔔 Управление подписками", callback_data="nav_subs")]
    ])

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Добро пожаловать! Я бот для отслеживания игр лиги chlhl.ru.\n"
        "Выберите интересующий вас раздел:",
        reply_markup=main_menu_kb()
    )

# Выбор дивизиона
@dp.callback_query(F.data.startswith("nav_"))
async def handle_navigation(callback: CallbackQuery):
    section = callback.data.split("_")[1]
    divisions = await db.get_all_divisions()

    if not divisions:
        await callback.message.edit_text(
            "База данных обновляется. Пожалуйста, попробуйте через пару секунд.",
            reply_markup=main_menu_kb()
        )
        return

    buttons = [
        [InlineKeyboardButton(text=d["name"], callback_data=f"div_{section}_{d['id']}")]
        for d in divisions
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")])
    
    await callback.message.edit_text("Выберите дивизион:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

# Выбор команды внутри дивизиона
@dp.callback_query(F.data.startswith("div_"))
async def handle_division_select(callback: CallbackQuery):
    _, section, div_id = callback.data.split("_")
    div_id = int(div_id)
    teams = await db.get_teams_by_division(div_id)

    buttons = []
    # Возможность смотреть матчи всего дивизиона сразу
    if section in ["upcoming", "past"]:
        buttons.append([InlineKeyboardButton(text="📌 Все игры дивизиона", callback_data=f"view_{section}_div_{div_id}")])
    elif section == "subs":
        buttons.append([InlineKeyboardButton(text="🔔 Подписаться на дивизион целиком", callback_data=f"sub_division_{div_id}")])

    for t in teams:
        if section == "subs":
            buttons.append([InlineKeyboardButton(text=t["name"], callback_data=f"sub_team_{t['id']}")])
        else:
            buttons.append([InlineKeyboardButton(text=t["name"], callback_data=f"view_{section}_team_{t['id']}")])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад к дивизионам", callback_data=f"nav_{section}")])
    await callback.message.edit_text("Выберите команду:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

# Просмотр результатов / расписания
@dp.callback_query(F.data.startswith("view_"))
async def handle_games_view(callback: CallbackQuery):
    _, section, target_type, target_id = callback.data.split("_")
    target_id = int(target_id)

    if section == "upcoming":
        games = await db.get_upcoming_games(
            team_id=target_id if target_type == "team" else None,
            division_id=target_id if target_type == "div" else None
        )
        if not games:
            text = "📅 Предстоящих игр не найдено."
        else:
            text = "📅 **Предстоящие игры:**\n\n"
            for g in games:
                text += (
                    f"🏒 **{g['team_name']}** vs **{g['opponent']}**\n"
                    f"🗓 {g['game_date']} {g['game_time']}\n"
                    f"📍 {g['stadium']} ({g['address']})\n\n"
                )
    else:
        games = await db.get_past_games(
            team_id=target_id if target_type == "team" else None,
            division_id=target_id if target_type == "div" else None
        )
        if not games:
            text = "🏆 Прошедших игр не найдено."
        else:
            text = "🏆 **Прошедшие игры:**\n\n"
            for g in games:
                text += (
                    f"🏒 **{g['team_name']}** vs **{g['opponent']}**\n"
                    f"📊 Счет: **{g['score']}** | {g['game_date']}\n\n"
                )

    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_main")]
    ])
    await callback.message.edit_text(text, reply_markup=back_kb, parse_mode="Markdown")

# Переключение подписки
@dp.callback_query(F.data.startswith("sub_"))
async def handle_subscription_toggle(callback: CallbackQuery):
    _, sub_type, target_id = callback.data.split("_")
    target_id = int(target_id)
    
    is_subscribed = await db.toggle_subscription(callback.from_user.id, sub_type, target_id)
    state_msg = "оформлена ✅" if is_subscribed else "снята ❌"
    
    await callback.answer(f"Подписка успешно {state_msg}!", show_alert=True)

@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu_kb())

async def run_parser_job():
    print("Запуск планового сбора данных chlhl.ru...")
    await parser.parse_and_sync_all()

async def main():
    await db.init_db()
    
    # 1. Запуск парсинга сразу при старте
    asyncio.create_task(run_parser_job())

    # 2. Настройка планировщика на 08:00, 13:00, 18:00, 23:00, 23:55 (MSK)
    scheduler.add_job(run_parser_job, 'cron', hour=8, minute=0)
    scheduler.add_job(run_parser_job, 'cron', hour=13, minute=0)
    scheduler.add_job(run_parser_job, 'cron', hour=18, minute=0)
    scheduler.add_job(run_parser_job, 'cron', hour=23, minute=0)
    scheduler.add_job(run_parser_job, 'cron', hour=23, minute=55)
    scheduler.start()

    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())