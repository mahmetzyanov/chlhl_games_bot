import aiohttp
from bs4 import BeautifulSoup
import asyncio
import re
import logging

async def fetch(session, url, retries=2):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for _ in range(retries):
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.text()
        except Exception as e:
            logging.error(f"Fetch error {url}: {e}")
            await asyncio.sleep(1)
    return None

async def run_scraper():
    async with aiohttp.ClientSession() as session:
        html = await fetch(session, 'https://chlhl.ru/')
        if not html: return [], [], []
            
        soup = BeautifulSoup(html, 'lxml')
        nav_links = soup.find_all('a', class_='blk-nav__drop-link')
        divisions = []
        for link in nav_links:
            name = link.text.strip()
            href = link.get('href', '')
            # Игнорируем общую страницу "Чемпионат" и завершенные турниры
            if '/tournament/' in href and name not in ['Чемпионат', 'Матч Открытия']:
                divisions.append({
                    'name': name,
                    'url': 'https://chlhl.ru' + href,
                    'chlhl_id': int(href.split('/')[-1])
                })
        
        # Собираем команды
        all_teams = []
        tasks = [parse_teams(session, div) for div in divisions]
        results = await asyncio.gather(*tasks)
        for div, teams in zip(divisions, results):
            for t in teams:
                t['division_chlhl_id'] = div['chlhl_id']
                all_teams.append(t)
                
        # Собираем список матчей
        all_matches = []
        match_tasks = [parse_matches_list(session, div) for div in divisions]
        match_results = await asyncio.gather(*match_tasks)
        raw_matches = []
        for div, matches in zip(divisions, match_results):
            for m in matches:
                m['division_chlhl_id'] = div['chlhl_id']
                raw_matches.append(m)
                
        # Асинхронно идем на страницу каждого матча, чтобы узнать стадион и адрес
        sem = asyncio.Semaphore(20) # Ограничение, чтобы не положить сайт
        async def fetch_with_sem(m):
            async with sem:
                return await parse_match_details(session, m)
                
        detailed_matches = await asyncio.gather(*[fetch_with_sem(m) for m in raw_matches])
        return divisions, all_teams, detailed_matches

async def parse_teams(session, division):
    html = await fetch(session, f"{division['url']}/teams")
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    teams = []
    
    links = soup.find_all('a', class_='blk-tteams__link')
    for link in links:
        name_div = link.find('div', class_='blk-tteams__name')
        name = name_div.text.strip() if name_div else ""
        m = re.search(r'team_id=(\d+)', link.get('href', ''))
        if m and name:
            teams.append({'chlhl_id': int(m.group(1)), 'name': name})
    return teams

async def parse_matches_list(session, division):
    html = await fetch(session, division['url'])
    if not html: return []
    soup = BeautifulSoup(html, 'lxml')
    matches = []
    
    items = soup.find_all('div', class_='blk-tprofile__mo-item')
    for item in items:
        link = item.find('a', class_='blk-tprofile__mo-link')
        if not link: continue
        
        match_id = re.search(r'/match/(\d+)', link.get('href', ''))
        if not match_id: continue
        
        date_time = item.find('div', class_='blk-tprofile__mo-date-time')
        dt_text = date_time.text.strip() if date_time else ""
        parts = [p.strip() for p in dt_text.split("/")]
        date_str = f"{parts[0]} {parts[1]}" if len(parts) >= 2 else dt_text
        time_str = parts[2] if len(parts) >= 3 else ""
        
        teams = [t.text.strip() for t in item.find_all('div', class_='blk-tprofile__mo-team')]
        scores = [s.text.strip() for s in item.find_all('td', class_='blk-tprofile__mo-score-cell--goals')]
        is_played = scores and scores[0] != '-'
        
        matches.append({
            'chlhl_id': int(match_id.group(1)), 'date': date_str, 'time': time_str,
            'team1_name': teams[0] if len(teams) > 0 else '', 'team2_name': teams[1] if len(teams) > 1 else '',
            'score1': int(scores[0]) if is_played else None, 'score2': int(scores[1]) if is_played else None,
            'is_played': is_played
        })
    return matches

async def parse_match_details(session, match_data):
    html = await fetch(session, f"https://chlhl.ru/match/{match_data['chlhl_id']}")
    if not html: return match_data
    
    soup = BeautifulSoup(html, 'lxml')
    stadium_el = soup.find('div', class_='blk-matchheader__stadium')
    if stadium_el:
        title = stadium_el.get('title', '')
        parts = title.split(',')
        match_data['stadium'] = parts[0].strip()
        match_data['address'] = ','.join(parts[1:]).strip() if len(parts) > 1 else ""
    
    team_links = soup.find_all('a', class_='blk-matchheader__team-link')
    for i, tl in enumerate(team_links[:2]):
        m = re.search(r'team_id=(\d+)', tl.get('href', ''))
        if m: match_data[f'team{i+1}_id'] = int(m.group(1))
    return match_data