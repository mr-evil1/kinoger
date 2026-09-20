import json
import os
import re
import time

from playwright.sync_api import sync_playwright

BASE        = 'https://kinoger.com'
SERIES_PATH = '/main/serie/'
OUT_DIR     = os.path.join(os.path.dirname(__file__), '..', 'data')
TIMEOUT     = 45_000
WAIT_CF     = 30
UA          = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
)

PAGES = {
    'kino':   BASE + '/',
    'movies': BASE + '/',
    'series': BASE + SERIES_PATH,
    'anime':  BASE + '/main/anime/',
}

GENRE_SKIP = ('erwachsene', 'erotik', 'na-russkom', 'xxx')


def _is_challenge(page):
    t = page.title().lower()
    return 'verification' in t or 'checking' in t


def _wait_cf(page):
    deadline = time.time() + WAIT_CF
    while time.time() < deadline and _is_challenge(page):
        time.sleep(1)
    if _is_challenge(page):
        raise RuntimeError('Cloudflare challenge not solved')
    time.sleep(3)


def _parse_entries(html, is_series=False):
    items = []
    for block in html.split('<div class="short">')[1:]:
        link_m = re.search(
            r'class=["\']begin["\'][^>]*>.*?<a href="(https?://[^"]+\.html)"[^>]*>([^<]+)</a>',
            block, re.S)
        if not link_m:
            continue
        href  = link_m.group(1)
        name  = link_m.group(2).strip()
        thumb_m = re.search(r'<!--dle_image_begin:([^|>]+)\|', block)
        if not thumb_m:
            thumb_m = re.search(r'<img[^>]+src="((?:https?://|/uploads/)[^"]+)"', block)
        thumb = thumb_m.group(1) if thumb_m else ''
        if thumb.startswith('/'):
            thumb = BASE + thumb
        item_is_series = is_series or bool(
            re.search(r'text-align:right[^>]*>(?:<[^>]+>)*\s*S\d{1,2}', block[:1000])
        )
        items.append({
            'title':     name,
            'url':       href,
            'poster':    thumb,
            'mediatype': 'tvshow' if item_is_series else 'movie',
        })
    return items


def _parse_genres(html):
    skip = GENRE_SKIP
    seen = set()
    items = []
    for m in re.finditer(
        r'<li[^>]+class=["\']links["\'][^>]*>\s*<a\s+href="(/main/[^"]+)"[^>]*>(.*?)</a>',
        html, re.S | re.I
    ):
        href = m.group(1)
        if any(s in href for s in skip) or href in seen:
            continue
        seen.add(href)
        name = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not name:
            continue
        items.append({'title': name, 'url': BASE + href})
    return items


def _fetch_page(ctx, url):
    page = ctx.new_page()
    page.goto(url, wait_until='domcontentloaded', timeout=TIMEOUT)
    _wait_cf(page)
    html = page.content()
    page.close()
    return html


def _fetch_genre_pages(ctx, genres):
    result = {}
    for genre in genres:
        try:
            html  = _fetch_page(ctx, genre['url'])
            items = _parse_entries(html)
            result[genre['url']] = items
            print(f'  Genre {genre["title"]}: {len(items)} items')
        except Exception as e:
            print(f'  Genre {genre["title"]} FEHLER: {e}')
    return result


def scrape():
    os.makedirs(OUT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
            ],
        )
        ctx = browser.new_context(
            user_agent=UA,
            viewport={'width': 1280, 'height': 720},
            locale='de-DE',
            timezone_id='Europe/Vienna',
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )

        data = {}

        for key, url in PAGES.items():
            print(f'Scraping {key}: {url}')
            try:
                html  = _fetch_page(ctx, url)
                is_s  = key == 'series'
                items = _parse_entries(html, is_series=is_s)
                if key == 'movies':
                    items = [i for i in items if i['mediatype'] == 'movie']
                data[key] = items
                print(f'  {len(items)} items')

                if key == 'kino':
                    genres = _parse_genres(html)
                    print(f'  {len(genres)} Genres gefunden, scrape...')
                    data['genres']     = genres
                    data['genre_data'] = _fetch_genre_pages(ctx, genres)

            except Exception as e:
                print(f'FEHLER {key}: {e}')
                data[key] = []

        cookies = ctx.cookies()
        cf = {c['name']: c['value'] for c in cookies
              if c['name'] in ('cf_clearance', 'PHPSESSID')}
        data['_cookies'] = cf
        data['_ts']      = int(time.time())

        browser.close()

    for key in ('kino', 'movies', 'series', 'anime', 'genres'):
        out = os.path.join(OUT_DIR, f'{key}.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(data.get(key, []), f, ensure_ascii=False, indent=2)
        print(f'Gespeichert: {out}')

    if data.get('genre_data'):
        out = os.path.join(OUT_DIR, 'genre_data.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(data['genre_data'], f, ensure_ascii=False, indent=2)
        print(f'Gespeichert: {out}')

    if cf:
        out = os.path.join(OUT_DIR, '..', 'cookies', 'kinoger.json')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w') as f:
            json.dump(cf, f, indent=2)
        print(f'Cookie gespeichert: {cf}')


if __name__ == '__main__':
    scrape()
