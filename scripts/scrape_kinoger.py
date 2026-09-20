import json
import os
import re
import time

from curl_cffi import requests as cf_requests

BASE     = 'https://kinoger.com'
OUT_DIR  = os.path.join(os.path.dirname(__file__), '..', 'data')
HTML_DIR = os.path.join(os.path.dirname(__file__), '..', 'html')

PAGES = {
    'movies': BASE + '/',
    'series': BASE + '/stream/serie/',
    'anime':  BASE + '/stream/anime/',
}

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

GENRE_SKIP = ()


def _title_from_url(url):
    m = re.search(r'/stream/\d+-(.+?)-stream(?:-deutsch.*?)?\.html', url)
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
            r'class=["\'\']begin["\'\'][^>]*>.*?<a href="(https?://[^"]+\.html)"[^>]*>([^<]+)</a>',
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
        r'<li[^>]+class=["\']links["\'][^>]*>\s*<a\s+href="(/(?:main|stream)/[^"]+)"[^>]*>(.*?)</a>',
        html, re.S | re.I
    ):
        href = m.group(1)
        if any(s in href for s in GENRE_SKIP) or href in seen:
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


def _is_cf_html(html):
    return 'cf_chl_opt' in html[:2000] or 'just a moment' in html[:500].lower() or 'sicherheitsüberprüfung' in html[:1000].lower()


def _save_html(name, html):
    if _is_cf_html(html):
        print(f'  {name}: CF-Challenge, nicht gespeichert.', flush=True)
        return False
    os.makedirs(HTML_DIR, exist_ok=True)
    path = os.path.join(HTML_DIR, name + '.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return True


def _fetch_page(session, url):
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def _fetch_all_pages(session, start_url, is_series=False, label='', save_html=False):
    items = []
    url = start_url
    page_num = 1
    seen_urls = set()
    while url:
        if url in seen_urls:
            break
        seen_urls.add(url)
        try:
            html = _fetch_page(session, url)
            if save_html:
                fname = _slug(label) + (f'_p{page_num}' if page_num > 1 else '')
                _save_html(fname, html)
            batch = _parse_entries(html, is_series=is_series)
            items.extend(batch)
            print(f'  {label} Seite {page_num}: {len(batch)} items (gesamt {len(items)})', flush=True)
            url = _parse_next_page(html, url)
            page_num += 1
        except Exception as e:
            print(f'  {label} Seite {page_num} FEHLER: {e}', flush=True)
            break
    return items


def scrape():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(HTML_DIR, exist_ok=True)

    session = cf_requests.Session(impersonate='chrome120')
    session.headers.update({'Accept-Language': 'de-DE,de;q=0.9'})

    data = {}

    print('Lade Startseite für Genre-Liste...', flush=True)
    try:
        home_html = _fetch_page(session, BASE + '/')
        _save_html('home', home_html)
        genres = _parse_genres(home_html)
        print(f'Genres aus Seite: {len(genres)}', flush=True)
    except Exception as e:
        print(f'Genre-Parsing FEHLER: {e}', flush=True)
        genres = []

    if not genres:
        print('Nutze Fallback-Genre-Liste.', flush=True)
        genres = FALLBACK_GENRES

    data['genres'] = genres

    for key, url in PAGES.items():
        print(f'Scraping {key}: {url}', flush=True)
        try:
            is_s  = key in ('series', 'anime')
            items = _fetch_all_pages(session, url, is_series=is_s, label=key, save_html=True)
            if key == 'movies':
                items = [i for i in items if i.get('mediatype') == 'movie']
            data[key] = items
            print(f'  {key}: {len(items)} items gesamt', flush=True)
        except Exception as e:
            print(f'FEHLER {key}: {e}', flush=True)
            data[key] = []

    print(f'Scrape Genres ({len(genres)})...', flush=True)
    genre_data = {}
    for genre in genres:
        try:
            items = _fetch_all_pages(session, genre['url'], label=genre['title'], save_html=True)
            genre_data[genre['url']] = items
            print(f'  Genre {genre["title"]}: {len(items)} items gesamt', flush=True)
        except Exception as e:
            print(f'  Genre {genre["title"]} FEHLER: {e}', flush=True)
            genre_data[genre['url']] = []
    data['genre_data'] = genre_data

    for key in ('movies', 'series', 'anime', 'genres'):
        out = os.path.join(OUT_DIR, f'{key}.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(data.get(key, []), f, ensure_ascii=False, indent=2)
        print(f'Gespeichert: {out}', flush=True)

    if data.get('genre_data'):
        out = os.path.join(OUT_DIR, 'genre_data.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(data['genre_data'], f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    scrape()
