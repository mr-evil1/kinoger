import json
import os
import re
import time

from playwright.sync_api import sync_playwright

BASE        = 'https://kinoger.com'
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
    'series': BASE + '/main/serie/',
    'anime':  BASE + '/main/anime/',
}

GENRE_SKIP = ('erwachsene', 'erotik', 'na-russkom', 'xxx')

GENRES_STATIC = [
    {'title': 'Action',        'url': BASE + '/main/action/'},
    {'title': 'Abenteuer',     'url': BASE + '/main/abenteuer/'},
    {'title': 'Animation',     'url': BASE + '/main/animation/'},
    {'title': 'Biografie',     'url': BASE + '/main/biografie/'},
    {'title': 'Drama',         'url': BASE + '/main/drama/'},
    {'title': 'Fantasy',       'url': BASE + '/main/fantasy/'},
    {'title': 'Geschichte',    'url': BASE + '/main/geschichte/'},
    {'title': 'Horror',        'url': BASE + '/main/horror/'},
    {'title': 'Komödie',       'url': BASE + '/main/komoedie/'},
    {'title': 'Krimi',         'url': BASE + '/main/krimi/'},
    {'title': 'Kriegsfilm',    'url': BASE + '/main/kriegsfilm/'},
    {'title': 'Musik',         'url': BASE + '/main/musik/'},
    {'title': 'Mystery',       'url': BASE + '/main/mystery/'},
    {'title': 'Romance',       'url': BASE + '/main/romance/'},
    {'title': 'Science-Fiction','url': BASE + '/main/science-fiction/'},
    {'title': 'Sport',         'url': BASE + '/main/sport/'},
    {'title': 'Thriller',      'url': BASE + '/main/thriller/'},
    {'title': 'Western',       'url': BASE + '/main/western/'},
    {'title': 'Dokumentarfilm','url': BASE + '/main/dokumentarfilm/'},
    {'title': 'Familie',       'url': BASE + '/main/familie/'},
]


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


def _parse_detail(html, url):
    be_codes = re.findall(r'kinoger\.be/v/([A-Za-z0-9]+)', html)
    pw_codes = re.findall(r'kinoger\.pw/e/([A-Za-z0-9]+)', html)
    filecode_be = be_codes[0] if be_codes else ''
    filecode_pw = pw_codes[0] if pw_codes else ''

    plot = ''
    m = re.search(r'<!--dle_image_end-->(.*?)(?=<hr|<center|<div class="footercontrol"|<div class="footerbar")', html, re.S | re.I)
    if not m:
        m = re.search(r'<div[^>]*class="[^"]*content_text[^"]*"[^>]*>(.*?)(?=<div class="footercontrol"|<div class="footerbar")', html, re.S | re.I)
    if m:
        raw = re.sub(r'<!--.*?-->', '', m.group(1), flags=re.S)
        raw = re.sub(r'<[^>]+>', '', raw)
        plot = re.sub(r'\s+', ' ', raw).strip()

    return {
        'filecode_be': filecode_be,
        'filecode_pw': filecode_pw,
        'plot':        plot[:500],
    }


def _parse_genres(html):
    seen  = set()
    items = []
    for m in re.finditer(
        r'<li[^>]+class=["\']links["\'][^>]*>\s*<a\s+href="(/main/[^"]+)"[^>]*>(.*?)</a>',
        html, re.S | re.I
    ):
        href = m.group(1)
        if any(s in href for s in GENRE_SKIP) or href in seen:
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


def _enrich_with_details(ctx, items, label=''):
    for i, item in enumerate(items):
        try:
            html   = _fetch_page(ctx, item['url'])
            detail = _parse_detail(html, item['url'])
            item.update(detail)
            if i % 5 == 0:
                print(f'  {label} {i+1}/{len(items)}: {item["title"][:40]}')
        except Exception as e:
            print(f'  Detail FEHLER {item["url"]}: {e}')
    return items


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
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled'],
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

        data['genres'] = GENRES_STATIC
        print(f'Genres: {len(GENRES_STATIC)} (statisch)')

        for key, url in PAGES.items():
            print(f'Scraping {key}: {url}')
            try:
                html  = _fetch_page(ctx, url)
                is_s  = key == 'series'
                items = _parse_entries(html, is_series=is_s)
                if key == 'movies':
                    items = [i for i in items if i.get('mediatype') == 'movie']
                items     = _enrich_with_details(ctx, items, key)
                data[key] = items
                print(f'  {key}: {len(items)} items')
            except Exception as e:
                print(f'FEHLER {key}: {e}')
                data[key] = []

        print(f'Scrape Genres ({len(GENRES_STATIC)})...')
        data['genre_data'] = _fetch_genre_pages(ctx, GENRES_STATIC)

        cookies = ctx.cookies()
        cf = {c['name']: c['value'] for c in cookies
              if c['name'] in ('cf_clearance', 'PHPSESSID')}
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

    if cf:
        out = os.path.join(OUT_DIR, '..', 'cookies', 'kinoger.json')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w') as f:
            json.dump(cf, f, indent=2)
        print(f'Cookie: {cf}')


if __name__ == '__main__':
    scrape()
