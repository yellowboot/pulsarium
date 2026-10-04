"""Shared theme assets for full Pulsarium pages and an idempotent batch upgrade.

Run `python site_theme.py` after changing theme assets to refresh their versions
in all existing pages. The generator uses the same function for new pages.
The compact third-party Mood widget retains its explicit light/dark parameter.
"""

import argparse
import functools
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@functools.lru_cache(maxsize=None)
def asset_url(path: str) -> str:
    data = (ROOT / path.lstrip('/')).read_bytes().replace(b'\r\n', b'\n')
    version = hashlib.sha1(data).hexdigest()[:10]
    return f'{path}?v={version}'


def add_theme_assets(page: str) -> str:
    if not re.search(r'<header\b', page, re.I):
        return page
    # Removing only our own tags makes migrations and rebuilds repeatable.
    page = re.sub(r'<script\b[^>]*data-pulsarium-theme="bootstrap"[^>]*></script>\n?', '', page)
    page = re.sub(r'<link\b[^>]*data-pulsarium-theme="styles"[^>]*>\n?', '', page)
    page = re.sub(r'href="/assets/site\.css(?:\?v=[^"\s]*)?"',
                  lambda match: f'href="{asset_url("/assets/site.css")}"', page)
    bootstrap = f'<script src="{asset_url("/assets/theme.js")}" data-pulsarium-theme="bootstrap"></script>\n'
    styles = f'<link rel="stylesheet" href="{asset_url("/assets/themes.css")}" data-pulsarium-theme="styles">\n'
    page, count = re.subn(r'(<meta\b[^>]*charset=[^>]+>\n?)', lambda match: match[1] + bootstrap, page, count=1, flags=re.I)
    if count != 1:
        raise ValueError('Full site page is missing its charset declaration')
    return page.replace('</head>', styles + '</head>', 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='check all full site pages without writing')
    args = parser.parse_args()
    changed = []
    total = 0
    for path in sorted(ROOT.rglob('*.html')):
        if '.git' in path.parts:
            continue
        original = path.read_text(encoding='utf-8')
        if not re.search(r'<header\b', original, re.I):
            continue
        total += 1
        updated = add_theme_assets(original)
        if updated != original:
            changed.append(path.relative_to(ROOT))
            if not args.check:
                path.write_text(updated, encoding='utf-8')
    print(f'Theme support: {total} full site pages; {len(changed)} ' + ('need an update' if args.check else 'updated'))
    if args.check and changed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
