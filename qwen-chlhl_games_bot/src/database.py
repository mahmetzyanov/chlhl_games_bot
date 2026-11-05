import aiosqlite

DB_NAME = 'chlhl_bot.db'

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS divisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chlhl_id INTEGER UNIQUE,
            name TEXT,
            url TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chlhl_id INTEGER UNIQUE,
            division_id INTEGER,
            name TEXT,
            FOREIGN KEY (division_id) REFERENCES divisions(id)
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chlhl_id INTEGER UNIQUE,
            division_id INTEGER,
            team1_id INTEGER,
            team2_id INTEGER,
            team1_name TEXT,
            team2_name TEXT,
            date TEXT,
            time TEXT,
            stadium TEXT,
            address TEXT,
            score1 INTEGER,
            score2 INTEGER,
            is_played BOOLEAN,
            FOREIGN KEY (division_id) REFERENCES divisions(id)
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            division_id INTEGER,
            team_id INTEGER
        )''')
        await db.commit()

async def save_divisions(divisions):
    async with aiosqlite.connect(DB_NAME) as db:
        mapping = {}
        for d in divisions:
            await db.execute("INSERT OR IGNORE INTO divisions (chlhl_id, name, url) VALUES (?, ?, ?)",
                             (d['chlhl_id'], d['name'], d['url']))
            await db.commit()
            cursor = await db.execute("SELECT id FROM divisions WHERE chlhl_id = ?", (d['chlhl_id'],))
            row = await cursor.fetchone()
            mapping[d['chlhl_id']] = row[0]
        return mapping

async def save_teams(teams, div_mapping):
    async with aiosqlite.connect(DB_NAME) as db:
        mapping = {}
        for t in teams:
            div_id = div_mapping.get(t['division_chlhl_id'])
            await db.execute("INSERT OR IGNORE INTO teams (chlhl_id, division_id, name) VALUES (?, ?, ?)",
                             (t['chlhl_id'], div_id, t['name']))
            await db.commit()
            cursor = await db.execute("SELECT id FROM teams WHERE chlhl_id = ?", (t['chlhl_id'],))
            row = await cursor.fetchone()
            mapping[t['chlhl_id']] = row[0]
        return mapping

async def save_matches(matches, div_mapping, team_mapping):
    new_matches = []
    async with aiosqlite.connect(DB_NAME) as db:
        for m in matches:
            div_id = div_mapping.get(m.get('division_chlhl_id'))
            t1_id = team_mapping.get(m.get('team1_id'))
            t2_id = team_mapping.get(m.get('team2_id'))
            
            cursor = await db.execute("SELECT id FROM matches WHERE chlhl_id = ?", (m['chlhl_id'],))
            existing = await cursor.fetchone()
            
            if not existing:
                await db.execute('''
                    INSERT INTO matches (chlhl_id, division_id, team1_id, team2_id, team1_name, team2_name, 
                                         date, time, stadium, address, score1, score2, is_played)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (m['chlhl_id'], div_id, t1_id, t2_id, m.get('team1_name'), m.get('team2_name'),
                      m.get('date'), m.get('time'), m.get('stadium'), m.get('address'), 
                      m.get('score1'), m.get('score2'), m.get('is_played')))
                new_matches.append(m)
            else:
                # Если матч уже был, но теперь появился счет — обновляем его
                if m.get('is_played'):
                    await db.execute('''
                        UPDATE matches SET score1=?, score2=?, is_played=? WHERE chlhl_id=?
                    ''', (m.get('score1'), m.get('score2'), m.get('is_played'), m['chlhl_id']))
        await db.commit()
    return new_matches

async def get_all_divisions():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM divisions ORDER BY name")
        return await cursor.fetchall()

async def get_teams_by_division(div_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM teams WHERE division_id = ? ORDER BY name", (div_id,))
        return await cursor.fetchall()

async def get_upcoming_matches_by_div(div_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM matches WHERE division_id = ? AND is_played = 0 ORDER BY date, time", (div_id,))
        return await cursor.fetchall()

async def get_upcoming_matches_by_team(team_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM matches WHERE (team1_id = ? OR team2_id = ?) AND is_played = 0 ORDER BY date, time", (team_id, team_id))
        return await cursor.fetchall()

async def get_past_matches_by_div(div_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM matches WHERE division_id = ? AND is_played = 1 ORDER BY date DESC LIMIT 20", (div_id,))
        return await cursor.fetchall()

async def get_past_matches_by_team(team_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM matches WHERE (team1_id = ? OR team2_id = ?) AND is_played = 1 ORDER BY date DESC LIMIT 20", (team_id, team_id))
        return await cursor.fetchall()

async def toggle_subscription(user_id, division_id=None, team_id=None):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        
        if division_id:
            cursor = await db.execute("SELECT id FROM subscriptions WHERE user_id = ? AND division_id = ?", (user_id, division_id))
            row = await cursor.fetchone()
            if row:
                await db.execute("DELETE FROM subscriptions WHERE id = ?", (row['id'],))
                await db.commit()
                return False
            else:
                await db.execute("INSERT INTO subscriptions (user_id, division_id) VALUES (?, ?)", (user_id, division_id))
                await db.commit()
                return True
        elif team_id:
            cursor = await db.execute("SELECT id FROM subscriptions WHERE user_id = ? AND team_id = ?", (user_id, team_id))
            row = await cursor.fetchone()
            if row:
                await db.execute("DELETE FROM subscriptions WHERE id = ?", (row['id'],))
                await db.commit()
                return False
            else:
                await db.execute("INSERT INTO subscriptions (user_id, team_id) VALUES (?, ?)", (user_id, team_id))
                await db.commit()
                return True

async def get_user_subscriptions(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT s.id as sub_id, d.id as div_id, t.id as team_id, d.name as div_name, t.name as team_name 
            FROM subscriptions s
            LEFT JOIN divisions d ON s.division_id = d.id
            LEFT JOIN teams t ON s.team_id = t.id
            WHERE s.user_id = ?
        ''', (user_id,))
        return await cursor.fetchall()