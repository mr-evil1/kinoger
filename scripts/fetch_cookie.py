import json
import os
import sys
import time

from playwright.sync_api import sync_playwright


TARGET   = 'https://kinoger.com/'
OUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'cookies', 'kinoger.json')
TIMEOUT  = 45_000
WAIT     = 8


def _is_challenge(page):
    return 'Verification' in page.title() or 'checking' in page.title().lower()


def fetch():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
            ],
        )
        ctx = browser.new_context(
            user_agent=(
                'Mozilla/5.0 (X11; Linux x86_64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/126.0.0.0 Safari/537.36'
            ),
            viewport={'width': 1280, 'height': 720},
            locale='de-DE',
            timezone_id='Europe/Vienna',
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)
        page = ctx.new_page()
        page.goto(TARGET, wait_until='domcontentloaded', timeout=TIMEOUT)

        deadline = time.time() + 30
        while time.time() < deadline and _is_challenge(page):
            time.sleep(1)

        if _is_challenge(page):
            print('ERROR: challenge not solved within 30s', file=sys.stderr)
            browser.close()
            sys.exit(1)

        time.sleep(WAIT)

        cookies = ctx.cookies()
        wanted  = {c['name']: c['value'] for c in cookies
                   if c['name'] in ('cf_clearance', 'PHPSESSID', '__ddgid_')}

        if 'cf_clearance' not in wanted:
            print('ERROR: cf_clearance not found in cookies', file=sys.stderr)
            print('Got:', [c['name'] for c in cookies], file=sys.stderr)
            browser.close()
            sys.exit(1)

        out_dir = os.path.dirname(OUT_PATH)
        os.makedirs(out_dir, exist_ok=True)
        with open(OUT_PATH, 'w') as fh:
            json.dump(wanted, fh, indent=2)

        print('OK:', wanted)
        browser.close()


if __name__ == '__main__':
    fetch()

