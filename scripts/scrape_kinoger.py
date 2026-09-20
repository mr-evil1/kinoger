import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.sync_api import sync_playwright

BASE       = 'https://kinoger.com'
OUT_DIR    = os.path.join(os.path.dirname(__file__), '..', 'data')
TIMEOUT    = 45_000
WAIT_CF    = 30
WORKERS    = 4
UA         = (
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

_print_lock = threading.Lock()

def _log(msg):
    with _print_lock:
        print(msg, flush=True)


def _is_challenge(page):
    t = page.title().lower()
    return 'verification' in t or 'checking' in t


def _wait_cf(page):
    if not _is_challenge(page):
        return
    deadline = time.time() + WAIT_CF
    while time.time() < deadline and _is_challenge(page):
        time.sleep(1)
    if _is_challenge(page):
        raise RuntimeError('Cloudflare challenge not solved')
    time.sleep(2)


def _parse_entries(html, is_series=False):
    items = []
    for block in html.split('<div class="short">')[1:]:
        link_m = re.search(
            r'class=["\']begin["\'][^>]*>.*?<a href="(https?://[^"]+\.html)"[^>]*>([^<]+)</a>',
            block, re.S)
        if not link_m:
            continue
        href = link_m.group(1)
        name = link_m.group(2).strip()
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


def _make_context(browser):
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
    return ctx


def _fetch_page(ctx, url):
    page = ctx.new_page()
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=TIMEOUT)
        _wait_cf(page)
        return page.content()
    finally:
        page.close()


def _collect_all_page_urls(ctx, start_url, label=''):
    urls = []
    url = start_url
    seen = set()
    while url:
        if url in seen:
            break
        seen.add(url)
        try:
            html = _fetch_page(ctx, url)
            urls.append((url, html))
            next_url = _parse_next_page(html, url)
            _log(f'  {label} entdeckt Seite {len(urls)}: {url}')
            url = next_url
        except Exception as e:
            _log(f'  {label} FEHLER bei {url}: {e}')
            break
    return urls


def _fetch_all_pages(browser, start_url, is_series=False, label=''):
    """Sequentiell — Playwright sync_api ist nicht thread-safe."""
    _log(f'  {label} sammle Seiten-URLs...')
    ctx = _make_context(browser)
    try:
        page_data = _collect_all_page_urls(ctx, start_url, label=label)
    finally:
        ctx.close()

    items = []
    for idx, (url, html) in enumerate(page_data):
        try:
            batch = _parse_entries(html, is_series=is_series)
            _log(f'  {label} Seite {idx+1}: {len(batch)} items')
            items.extend(batch)
        except Exception as e:
            _log(f'  {label} Parse FEHLER Seite {idx+1}: {e}')

    _log(f'  {label}: {len(items)} items gesamt')
    return items


def scrape():
    os.makedirs(OUT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled'],
        )

        # Genre-Liste von Startseite holen
        ctx0 = _make_context(browser)
        _log('Lade Startseite für Genre-Liste...')
        try:
            home_html = _fetch_page(ctx0, BASE + '/')
            genres    = _parse_genres(home_html)
            _log(f'Genres gefunden: {len(genres)}')
        except Exception as e:
            _log(f'Genre-Parsing FEHLER: {e}')
            genres = []
        finally:
            ctx0.close()

        data = {'genres': genres}

        # Hauptkategorien scrapen
        for key, url in PAGES.items():
            _log(f'Scraping {key}: {url}')
            try:
                is_s      = key in ('series', 'anime')
                items     = _fetch_all_pages(browser, url, is_series=is_s, label=key)
                if key == 'movies':
                    items = [i for i in items if i.get('mediatype') == 'movie']
                data[key] = items
            except Exception as e:
                _log(f'FEHLER {key}: {e}')
                data[key] = []

        # Genres sequentiell scrapen (kein Threading mit Playwright sync!)
        _log(f'Scrape Genres ({len(genres)}) sequentiell...')
        genre_data = {}
        for genre in genres:
            _log(f'  Genre: {genre["title"]}')
            try:
                items = _fetch_all_pages(browser, genre['url'], label=genre['title'])
                genre_data[genre['url']] = items
            except Exception as e:
                _log(f'  Genre {genre["title"]} FEHLER: {e}')
                genre_data[genre['url']] = []
        data['genre_data'] = genre_data

        # Cookies sichern
        ctx_final = _make_context(browser)
        cookies   = ctx_final.cookies()
        ctx_final.close()
        cf = {c['name']: c['value'] for c in cookies
              if c['name'] in ('cf_clearance', 'PHPSESSID')}
        browser.close()

    # Daten speichern
    for key in ('kino', 'movies', 'series', 'anime', 'genres'):
        out = os.path.join(OUT_DIR, f'{key}.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(data.get(key, []), f, ensure_ascii=False, indent=2)
        _log(f'Gespeichert: {out}')

    if data.get('genre_data'):
        out = os.path.join(OUT_DIR, 'genre_data.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(data['genre_data'], f, ensure_ascii=False, indent=2)

    if cf:
        out = os.path.join(OUT_DIR, '..', 'cookies', 'kinoger.json')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'w') as f:
            json.dump(cf, f, indent=2)
        _log(f'Cookie: {cf}')


if __name__ == '__main__':
    scrape()
