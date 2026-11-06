import aiosqlite

DB_NAME = "chlhl_hockey.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица дивизионов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS divisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                url TEXT
            )
        """)
        # Таблица команд
        await db.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                division_id INTEGER,
                name TEXT UNIQUE,
                url TEXT,
                FOREIGN KEY (division_id) REFERENCES divisions(id)
            )
        """)
        # Прошедшие игры
        await db.execute("""
            CREATE TABLE IF NOT EXISTS past_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER,
                game_date TEXT,
                game_time TEXT,
                opponent TEXT,
                score TEXT,
                stadium TEXT,
                address TEXT,
                UNIQUE(team_id, game_date, opponent)
            )
        """)
        # Предстоящие игры
        await db.execute("""
            CREATE TABLE IF NOT EXISTS upcoming_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER,
                game_date TEXT,
                game_time TEXT,
                opponent TEXT,
                stadium TEXT,
                address TEXT,
                UNIQUE(team_id, game_date, opponent)
            )
        """)
        # Подписки пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                sub_type TEXT,  -- 'team' или 'division'
                target_id INTEGER,
                UNIQUE(user_id, sub_type, target_id)
            )
        """)
        await db.commit()

async def get_all_divisions():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM divisions ORDER BY name") as cursor:
            return await cursor.fetchall()

async def get_teams_by_division(division_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM teams WHERE division_id = ? ORDER BY name", (division_id,)) as cursor:
            return await cursor.fetchall()

async def get_team(team_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)) as cursor:
            return await cursor.fetchone()

async def get_upcoming_games(team_id: int = None, division_id: int = None):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        if team_id:
            query = """
                SELECT g.*, t.name as team_name FROM upcoming_games g
                JOIN teams t ON g.team_id = t.id
                WHERE g.team_id = ? ORDER BY g.game_date
            """
            params = (team_id,)
        elif division_id:
            query = """
                SELECT g.*, t.name as team_name FROM upcoming_games g
                JOIN teams t ON g.team_id = t.id
                WHERE t.division_id = ? ORDER BY g.game_date
            """
            params = (division_id,)
        else:
            return []
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()

async def get_past_games(team_id: int = None, division_id: int = None):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        if team_id:
            query = """
                SELECT g.*, t.name as team_name FROM past_games g
                JOIN teams t ON g.team_id = t.id
                WHERE g.team_id = ? ORDER BY g.game_date DESC LIMIT 10
            """
            params = (team_id,)
        elif division_id:
            query = """
                SELECT g.*, t.name as team_name FROM past_games g
                JOIN teams t ON g.team_id = t.id
                WHERE t.division_id = ? ORDER BY g.game_date DESC LIMIT 20
            """
            params = (division_id,)
        else:
            return []
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()

async def toggle_subscription(user_id: int, sub_type: str, target_id: int) -> bool:
    """Возвращает True, если подписка добавлена, False, если удалена."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id FROM user_subscriptions WHERE user_id = ? AND sub_type = ? AND target_id = ?",
            (user_id, sub_type, target_id)
        ) as cursor:
            row = await cursor.fetchone()
        
        if row:
            await db.execute("DELETE FROM user_subscriptions WHERE id = ?", (row[0],))
            await db.commit()
            return False
        else:
            await db.execute(
                "INSERT INTO user_subscriptions (user_id, sub_type, target_id) VALUES (?, ?, ?)",
                (user_id, sub_type, target_id)
            )
            await db.commit()
            return True

async def get_user_subscribers_for_team_or_division(team_id: int, division_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT DISTINCT user_id FROM user_subscriptions 
            WHERE (sub_type = 'team' AND target_id = ?) 
               OR (sub_type = 'division' AND target_id = ?)
        """, (team_id, division_id)) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]