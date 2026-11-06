import aiohttp
from bs4 import BeautifulSoup
import aiosqlite
from database import DB_NAME

BASE_URL = "https://chlhl.ru"

async def fetch_page(url: str, session: aiohttp.ClientSession) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        async with session.get(url, headers=headers, timeout=15) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception as e:
        print(f"Ошибка при загрузке {url}: {e}")
    return ""

async def parse_and_sync_all():
    """Сбор дивизионов, команд, расписания и сохранение в SQLite."""
    async with aiohttp.ClientSession() as session:
        main_html = await fetch_page(BASE_URL, session)
        if not main_html:
            return

        soup = BeautifulSoup(main_html, "html.parser")
        
        # Поиск ссылок на дивизионы (адаптируется под актуальные селекторы меню сайта)
        division_links = soup.select("nav a[href*='/division/'], .menu a[href*='/division/']")
        if not division_links:
            # Резервный поиск любых ссылок на дивизионы
            division_links = soup.find_all("a", href=lambda h: h and "/division/" in h)

        async with aiosqlite.connect(DB_NAME) as db:
            for d_link in division_links:
                div_name = d_link.get_text(strip=True)
                div_url = BASE_URL + d_link["href"] if d_link["href"].startswith("/") else d_link["href"]
                
                await db.execute(
                    "INSERT INTO divisions (name, url) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET url=excluded.url",
                    (div_name, div_url)
                )
                await db.commit()

                async with db.execute("SELECT id FROM divisions WHERE name = ?", (div_name,)) as cur:
                    div_row = await cur.fetchone()
                    div_id = div_row[0]

                # Парсинг страницы дивизиона для извлечения команд
                div_html = await fetch_page(div_url, session)
                div_soup = BeautifulSoup(div_html, "html.parser")
                team_links = div_soup.select("a[href*='/team/']")

                for t_link in team_links:
                    t_name = t_link.get_text(strip=True)
                    if not t_name:
                        continue
                    t_url = BASE_URL + t_link["href"] if t_link["href"].startswith("/") else t_link["href"]

                    await db.execute(
                        "INSERT INTO teams (division_id, name, url) VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET division_id=excluded.division_id, url=excluded.url",
                        (div_id, t_name, t_url)
                    )
                    await db.commit()

                    async with db.execute("SELECT id FROM teams WHERE name = ?", (t_name,)) as cur:
                        team_row = await cur.fetchone()
                        team_id = team_row[0]

                    # Сбор прошедших и будущих игр команды
                    team_html = await fetch_page(t_url, session)
                    t_soup = BeautifulSoup(team_html, "html.parser")
                    
                    # Разбор строк расписания (селекторы таблиц матчей)
                    match_rows = t_soup.select(".game-row, .calendar-item, table.games tr")
                    for row in match_rows:
                        text = row.get_text(" ", strip=True)
                        # Пример структуры извлечения полей матча:
                        date_elem = row.select_one(".date, .game-date")
                        time_elem = row.select_one(".time, .game-time")
                        opp_elem = row.select_one(".opponent, .team-2")
                        score_elem = row.select_one(".score, .result")
                        arena_elem = row.select_one(".stadium, .arena")
                        addr_elem = row.select_one(".address")

                        g_date = date_elem.get_text(strip=True) if date_elem else "Не указана"
                        g_time = time_elem.get_text(strip=True) if time_elem else ""
                        g_opp = opp_elem.get_text(strip=True) if opp_elem else "Соперник не указан"
                        g_score = score_elem.get_text(strip=True) if score_elem else None
                        g_stadium = arena_elem.get_text(strip=True) if arena_elem else "Арена уточняется"
                        g_addr = addr_elem.get_text(strip=True) if addr_elem else "Адрес уточняется"

                        if g_score and any(char.isdigit() for char in g_score):
                            # Прошедшая игра со счетом
                            await db.execute("""
                                INSERT INTO past_games (team_id, game_date, game_time, opponent, score, stadium, address)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(team_id, game_date, opponent) DO UPDATE SET score=excluded.score
                            """, (team_id, g_date, g_time, g_opp, g_score, g_stadium, g_addr))
                        else:
                            # Предстоящая игра
                            await db.execute("""
                                INSERT INTO upcoming_games (team_id, game_date, game_time, opponent, stadium, address)
                                VALUES (?, ?, ?, ?, ?, ?)
                                ON CONFLICT(team_id, game_date, opponent) DO UPDATE SET 
                                    game_time=excluded.game_time, stadium=excluded.stadium, address=excluded.address
                            """, (team_id, g_date, g_time, g_opp, g_stadium, g_addr))
                    await db.commit()