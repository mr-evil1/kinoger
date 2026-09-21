import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

TARGET   = 'https://kinoger.com/'
OUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'cookies', 'kinoger.json')
TIMEOUT  = 45_000
WAIT     = 8

UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/126.0.0.0 Safari/537.36'
)

INIT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    window.chrome = {runtime: {}};
    const _origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, ...args) {
        const ctx = _origGetContext.call(this, type, ...args);
        if ((type === 'webgl' || type === 'experimental-webgl') && ctx) {
            const _origExt = ctx.getExtension.bind(ctx);
            ctx.getExtension = function(name) {
                if (name === 'WEBGL_debug_renderer_info') return null;
                return _origExt(name);
            };
        }
        return ctx;
    };
"""


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
            user_agent=UA,
            viewport={'width': 1280, 'height': 720},
            locale='de-DE',
            timezone_id='Europe/Vienna',
        )
        ctx.add_init_script(INIT_SCRIPT)
        page = ctx.new_page()
        page.route('**/hostadminonline-waf-verify', _patch_waf)
        page.goto(TARGET, wait_until='domcontentloaded', timeout=TIMEOUT)

        deadline = time.time() + 60
        while time.time() < deadline and _is_challenge(page):
            time.sleep(1)

        if _is_challenge(page):
            print('ERROR: challenge not solved within 60s', file=sys.stderr)
            browser.close()
            sys.exit(1)

        time.sleep(WAIT)

        cookies = ctx.cookies()
        wanted = {c['name']: c['value'] for c in cookies
                  if c['domain'] in ('kinoger.com', '.kinoger.com')}

        if not wanted:
            print('ERROR: keine Cookies gefunden', file=sys.stderr)
            print('Got:', [c['name'] for c in cookies], file=sys.stderr)
            browser.close()
            sys.exit(1)

        out_dir = os.path.dirname(OUT_PATH)
        os.makedirs(out_dir, exist_ok=True)
        with open(OUT_PATH, 'w') as fh:
            json.dump(wanted, fh, indent=2)

        print('OK:', list(wanted.keys()))
        browser.close()


if __name__ == '__main__':
    fetch()
