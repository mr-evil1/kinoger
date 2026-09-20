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
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
)

PAGES = {
    'kino':   BASE + '/',
    'movies': BASE + '/',
    'series': BASE + '/main/serie/',
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
        be_codes = re.findall(r'kinoger\.be/v/([A-Za-z0-9]+)', block)
        pw_codes = re.findall(r'kinoger\.pw/e/([A-Za-z0-9]+)', block)
        items.append({
            'title':       name,
            'url':         href,
            'poster':      thumb,
            'mediatype':   'tvshow' if item_is_series else 'movie',
            'filecode_be': be_codes[0] if be_codes else '',
            'filecode_pw': pw_codes[0] if pw_codes else '',
        })
    return items


def _parse_next_page(html, current_url):
    m = re.search(r'<a href="([^"]+)"[^>]*>\s*vorw', html, re.I)
    if not m:
        return None
    href = m.group(1).strip()
    if href.startswith('/'):
        href = BASE + href
    if href == current_url:
        return None
    return href


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


def _fetch_all_pages(ctx, start_url, is_series=False, label=''):
    items = []
    url = start_url
    page_num = 1
    seen_urls = set()
    while url:
        if url in seen_urls:
            break
        seen_urls.add(url)
        try:
            html = _fetch_page(ctx, url)
            batch = _parse_entries(html, is_series=is_series)
            items.extend(batch)
            print(f'  {label} Seite {page_num}: {len(batch)} items (gesamt {len(items)})')
            url = _parse_next_page(html, url)
            page_num += 1
        except Exception as e:
            print(f'  {label} Seite {page_num} FEHLER: {e}')
            break
    return items


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

        print('Lade Startseite für Genre-Liste...')
        try:
            home_html = _fetch_page(ctx, BASE + '/')
            genres = _parse_genres(home_html)
            print(f'Genres gefunden: {len(genres)}')
        except Exception as e:
            print(f'Genre-Parsing FEHLER: {e}')
            genres = []
        data['genres'] = genres

        for key, url in PAGES.items():
            print(f'Scraping {key}: {url}')
            try:
                is_s  = key in ('series', 'anime')
                items = _fetch_all_pages(ctx, url, is_series=is_s, label=key)
                if key == 'movies':
                    items = [i for i in items if i.get('mediatype') == 'movie']
                data[key] = items
                print(f'  {key}: {len(items)} items gesamt')
            except Exception as e:
                print(f'FEHLER {key}: {e}')
                data[key] = []

        print(f'Scrape Genres ({len(genres)})...')
        genre_data = {}
        for genre in genres:
            try:
                items = _fetch_all_pages(ctx, genre['url'], label=genre['title'])
                genre_data[genre['url']] = items
                print(f'  Genre {genre["title"]}: {len(items)} items gesamt')
            except Exception as e:
                print(f'  Genre {genre["title"]} FEHLER: {e}')
                genre_data[genre['url']] = []
        data['genre_data'] = genre_data

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
