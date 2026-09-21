# by mr-evil1
import json
import os
import re
import subprocess
import time

from playwright.sync_api import sync_playwright

BASE           = 'https://kinoger.com'
DATA_DIR       = os.path.join(os.path.dirname(__file__), '..', 'data')
GENRES_DIR     = os.path.join(DATA_DIR, 'genres')
CATALOG_PATH   = os.path.join(DATA_DIR, 'catalog.json')
PROGRESS_PATH  = os.path.join(DATA_DIR, 'progress.json')
INDEX_PATH     = os.path.join(GENRES_DIR, 'index.json')

TIMEOUT       = 45_000
MAX_PAGES_RUN = 12
PAGE_DELAY    = 10
COOKIE_PATH   = os.path.join(os.path.dirname(__file__), '..', 'cookies', 'kinoger.json')

MAIN_SLUGS = {
    'movies': BASE + '/',
    'series': BASE + '/stream/serie/',
    'anime':  BASE + '/stream/anime/',
}

SERIES_SLUGS = {'series', 'anime', 'serie', 'tv_shows'}

FALLBACK_GENRES = [
    {'title': 'Anime',         'url': BASE + '/stream/anime/'},
    {'title': 'Action',        'url': BASE + '/stream/action/'},
    {'title': 'Animation',     'url': BASE + '/stream/animation/'},
    {'title': 'Abenteuer',     'url': BASE + '/stream/abenteuer/'},
    {'title': 'Biography',     'url': BASE + '/stream/biography/'},
    {'title': 'Bollywood',     'url': BASE + '/stream/bollywood/'},
    {'title': 'Drama',         'url': BASE + '/stream/drama/'},
    {'title': 'Dokumentation', 'url': BASE + '/stream/dokumentation/'},
    {'title': 'Englisch',      'url': BASE + '/stream/englisch/'},
    {'title': 'Erwachsene',    'url': BASE + '/stream/erwachsene/'},
    {'title': 'Familie',       'url': BASE + '/stream/familie/'},
    {'title': 'Fantasy',       'url': BASE + '/stream/fantasy/'},
    {'title': 'Geschichte',    'url': BASE + '/stream/geschichte/'},
    {'title': 'Horror',        'url': BASE + '/stream/horror/'},
    {'title': 'History',       'url': BASE + '/stream/history/'},
    {'title': 'Krimi',         'url': BASE + '/stream/krimi/'},
    {'title': 'Krieg',         'url': BASE + '/stream/krieg/'},
    {'title': 'Komödie',       'url': BASE + '/stream/komdie/'},
    {'title': 'Kurzfilm',      'url': BASE + '/stream/kurzfilm/'},
    {'title': 'Music',         'url': BASE + '/stream/music/'},
    {'title': 'Mystery',       'url': BASE + '/stream/mystery/'},
    {'title': 'Musical',       'url': BASE + '/stream/musical/'},
    {'title': 'Russisch',      'url': BASE + '/stream/na-russkom/'},
    {'title': 'Romance',       'url': BASE + '/stream/romance/'},
    {'title': 'Tv Shows',      'url': BASE + '/stream/tv-shows/'},
    {'title': 'Serie',         'url': BASE + '/stream/serie/'},
    {'title': 'Sport',         'url': BASE + '/stream/sport/'},
    {'title': 'Sci-Fi',        'url': BASE + '/stream/sci-fi/'},
    {'title': 'Thriller',      'url': BASE + '/stream/thriller/'},
    {'title': 'Trickfilm',     'url': BASE + '/stream/trickfilm/'},
    {'title': 'Western',       'url': BASE + '/stream/western/'},
    {'title': 'Zeichentrick',  'url': BASE + '/stream/zeichentrick/'},
]


def _title_from_url(url):
    m = re.search(r'/stream/\d+-(.+?)-stream(?:-deutsch.*)?.html', url)
    if not m:
        m = re.search(r'/stream/\d+-(.+?)\.html', url)
    if m:
        return m.group(1).replace('-', ' ').title()
    return url


def _parse_entries(html, is_series=False):
    seen  = set()
    items = []

    for block in html.split('<div class="short">')[1:]:
        link_m = re.search(
            r'class=["\'\'"]begin["\'\'"][^>]*>.*?<a href="(https?://[^"]+\.html)"[^>]*>([^<]+)</a>',
            block, re.S)
        if not link_m:
            continue
        href = link_m.group(1)
        if href in seen:
            continue
        seen.add(href)
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

    for m in re.finditer(
        r'<a href="(https?://kinoger\.com/stream/[^"]+\.html)"[^>]*>\s*<div class="item">\s*<img[^>]+src="([^"]+)"',
        html, re.S
    ):
        href = m.group(1)
        if href in seen:
            continue
        seen.add(href)
        thumb = m.group(2)
        if thumb.startswith('/'):
            thumb = BASE + thumb
        name = _title_from_url(href)
        item_is_series = is_series or '-s' in href or 'staffel' in href.lower() or 'serie' in href.lower()
        items.append({
            'title':       name,
            'url':         href,
            'poster':      thumb,
            'mediatype':   'tvshow' if item_is_series else 'movie',
            'filecode_be': '',
            'filecode_pw': '',
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
        r'<li[^>]+class=["\'"]links["\'"][^>]*>\s*<a\s+href="(/(?:main|stream)/[^"]+)"[^>]*>(.*?)</a>',
        html, re.S | re.I
    ):
        href = m.group(1)
        if href in seen:
            continue
        seen.add(href)
        name = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if not name:
            continue
        url = BASE + href.replace('/main/', '/stream/')
        items.append({'title': name, 'url': url})
    return items


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def _is_waf_html(html):
    h = html[:2000].lower()
    return (
        'cf_chl_opt' in h or
        'just a moment' in h or
        'sicherheitsüberprüfung' in h or
        'hostadminonline-waf-verify' in h or
        'verification...' in h or
        'access denied' in h
    )


def _load_genre(slug):
    path = os.path.join(GENRES_DIR, slug + '.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return []


def _save_genre(slug, items):
    os.makedirs(GENRES_DIR, exist_ok=True)
    path = os.path.join(GENRES_DIR, slug + '.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False)


def _update_genre_index(title, slug):
    os.makedirs(GENRES_DIR, exist_ok=True)
    index = []
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, encoding='utf-8') as f:
            index = json.load(f)
    if not any(g['slug'] == slug for g in index):
        index.append({'title': title, 'slug': slug})
        with open(INDEX_PATH, 'w', encoding='utf-8') as f:
            json.dump(index, f, ensure_ascii=False)


def _update_catalog(items):
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(CATALOG_PATH):
        with open(CATALOG_PATH, encoding='utf-8') as f:
            catalog = json.load(f)
    else:
        catalog = {}
    for item in items:
        url = item.get('url', '')
        if not url:
            continue
        existing = catalog.get(url, {})
        catalog[url] = {
            'title':       item.get('title') or existing.get('title', ''),
            'poster':      item.get('poster') or existing.get('poster', ''),
            'filecode_be': item.get('filecode_be') or existing.get('filecode_be', ''),
            'filecode_pw': item.get('filecode_pw') or existing.get('filecode_pw', ''),
            'mediatype':   item.get('mediatype') or existing.get('mediatype', 'movie'),
        }
    with open(CATALOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(catalog, f, ensure_ascii=False)


def _load_progress():
    if os.path.exists(PROGRESS_PATH):
        with open(PROGRESS_PATH) as f:
            return json.load(f)
    return {}


def _save_progress(progress):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROGRESS_PATH, 'w') as f:
        json.dump(progress, f, indent=2)


def _git_commit(label):
    try:
        subprocess.run(['git', 'add', 'data/'], check=False)
        result = subprocess.run(
            ['git', 'commit', '-m', 'scrape: %s [skip ci]' % label],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            subprocess.run(['git', 'pull', '--rebase', 'origin', 'main'], check=False)
            subprocess.run(['git', 'push'], check=False)
            print(f'  git: {label} committed & pushed', flush=True)
    except Exception as e:
        print(f'  git FEHLER: {e}', flush=True)


INIT_SCRIPT = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "window.chrome={runtime:{}};"
    "const _origGetContext=HTMLCanvasElement.prototype.getContext;"
    "HTMLCanvasElement.prototype.getContext=function(type,...args){"
    "const ctx=_origGetContext.call(this,type,...args);"
    "if((type==='webgl'||type==='experimental-webgl')&&ctx){"
    "const _o=ctx.getExtension.bind(ctx);"
    "ctx.getExtension=function(n){if(n==='WEBGL_debug_renderer_info')return null;return _o(n);};"
    "}return ctx;};"
)


def _patch_waf(route, request):
    try:
        body = json.loads(request.post_data or '{}')
        if 'botSignals' in body:
            sigs = body['botSignals']
            for k in ('software_gpu', 'chrome_missing', 'toString_tampered',
                      'iframe_webdriver', 'selenium', 'phantom', 'headless_agent'):
                sigs[k] = '0'
        route.continue_(post_data=json.dumps(body))
    except Exception:
        route.continue_()


def _is_challenge(page):
    t = page.title().lower()
    return 'verification' in t or 'checking' in t or 'just a moment' in t


def _wait_waf(page):
    if not _is_challenge(page):
        return
    print('  WAF-Challenge erkannt, warte...', flush=True)
    deadline = time.time() + 60
    while time.time() < deadline and _is_challenge(page):
        time.sleep(1)
    if _is_challenge(page):
        raise RuntimeError('WAF challenge not solved')
    print('  WAF-Challenge gelöst', flush=True)
    time.sleep(2)


def _fetch_page(ctx, url):
    page = ctx.new_page()
    page.route('**/hostadminonline-waf-verify', _patch_waf)
    page.goto(url, wait_until='domcontentloaded', timeout=TIMEOUT)
    page.wait_for_load_state('networkidle', timeout=TIMEOUT)
    _wait_waf(page)
    html = page.content()
    page.close()
    return html


def _load_cookies(ctx):
    if not os.path.exists(COOKIE_PATH):
        print('Kein Cookie gefunden.', flush=True)
        return
    with open(COOKIE_PATH) as f:
        saved = json.load(f)
    cookies = [{'name': k, 'value': v, 'domain': 'kinoger.com', 'path': '/'}
               for k, v in saved.items() if v]
    ctx.add_cookies(cookies)
    print(f'Cookie geladen: {list(saved.keys())}', flush=True)


def _fetch_and_save(ctx, slug, url, retries=3, delay=4, progress=None, is_series=False):
    if progress is None:
        progress = {}

    checkpoint  = progress.get(slug, {})
    page_num    = checkpoint.get('page', 1)
    current_url = checkpoint.get('next_url', url)
    pages_this_run = 0
    seen        = set()

    accumulated = _load_genre(slug)
    seen_urls   = {i['url'] for i in accumulated}

    if page_num > 1:
        print(f'  {slug}: Fortsetze ab Seite {page_num} ({len(accumulated)} items geladen)', flush=True)

    while current_url and current_url not in seen:
        if pages_this_run >= MAX_PAGES_RUN:
            progress[slug] = {'page': page_num, 'next_url': current_url}
            _save_progress(progress)
            print(f'  {slug}: Pause nach {MAX_PAGES_RUN} Seiten, nächster Run ab Seite {page_num}', flush=True)
            return

        seen.add(current_url)
        fname = slug if page_num == 1 else '%s_p%d' % (slug, page_num)
        print(f'  {fname}: {current_url}', flush=True)

        saved = False
        for attempt in range(1, retries + 1):
            try:
                html = _fetch_page(ctx, current_url)
                if _is_waf_html(html):
                    raise RuntimeError('WAF-Block')
                batch     = _parse_entries(html, is_series=is_series)
                new_items = [i for i in batch if i['url'] not in seen_urls]
                seen_urls.update(i['url'] for i in new_items)
                accumulated.extend(new_items)
                _update_catalog(new_items)
                _save_genre(slug, accumulated)
                _git_commit(fname)
                print(f'  {fname}: {len(new_items)} neu ({len(accumulated)} gesamt)', flush=True)
                current_url = _parse_next_page(html, current_url)
                page_num   += 1
                pages_this_run += 1
                saved = True
                if current_url:
                    time.sleep(PAGE_DELAY)
                break
            except Exception as e:
                print(f'  {fname} FEHLER (Versuch {attempt}/{retries}): {e}', flush=True)
                time.sleep(delay * attempt)

        if not saved:
            print(f'  {fname}: aufgegeben nach {retries} Versuchen', flush=True)
            break

    progress[slug] = {'page': page_num, 'next_url': None, 'done': True}
    _save_progress(progress)
    print(f'  {slug}: komplett ({page_num - 1} Seiten, {len(accumulated)} items)', flush=True)


def scrape():
    with sync_playwright() as p:
        headless = os.environ.get('PLAYWRIGHT_HEADLESS', '1') != '0'
        browser  = p.chromium.launch(
            headless=headless,
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled'],
        )
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 720},
            locale='de-DE',
            timezone_id='Europe/Vienna',
        )
        ctx.add_init_script(INIT_SCRIPT)
        _load_cookies(ctx)

        print('Lade Startseite...', flush=True)
        try:
            home_html   = _fetch_page(ctx, BASE + '/')
            home_items  = _parse_entries(home_html)
            _save_genre('home', home_items)
            _update_catalog(home_items)
            _git_commit('home')
            genres = _parse_genres(home_html)
            print(f'Genres aus Seite: {len(genres)}', flush=True)
        except Exception as e:
            print(f'Startseite FEHLER: {e}', flush=True)
            genres = []

        if not genres:
            print('Nutze Fallback-Genre-Liste.', flush=True)
            genres = FALLBACK_GENRES

        progress = _load_progress()

        done_count  = sum(1 for v in progress.values() if v.get('done'))
        remaining   = sum(1 for v in progress.values() if not v.get('done'))
        print(f'Checkpoint: {done_count} fertig, {remaining} noch offen', flush=True)

        print('Lade Haupt-Seiten...', flush=True)
        for slug, url in MAIN_SLUGS.items():
            if progress.get(slug, {}).get('done'):
                print(f'  {slug}: bereits fertig, überspringe.', flush=True)
                continue
            _fetch_and_save(ctx, slug, url, progress=progress, is_series=slug in SERIES_SLUGS)

        print(f'Lade Genre-Seiten ({len(genres)})...', flush=True)
        for genre in genres:
            slug = _slug(genre['title'])
            if progress.get(slug, {}).get('done'):
                print(f'  {slug}: bereits fertig, überspringe.', flush=True)
                _update_genre_index(genre['title'], slug)
                continue
            _fetch_and_save(ctx, slug, genre['url'], progress=progress, is_series=slug in SERIES_SLUGS)
            _update_genre_index(genre['title'], slug)
            time.sleep(PAGE_DELAY)

        browser.close()

    print('Fertig.', flush=True)


if __name__ == '__main__':
    scrape()
