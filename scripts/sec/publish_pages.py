"""Regenerate only SEC landing pages from the shared successful snapshots."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import site_build as site


def main():
    companies = site.build_companies()
    pages = [site.insider_page(side, companies) for side in site.INSIDER_PAGES]
    pages += [site.sector_analytics_page(companies), site.report_highlights_page(companies)]
    changed = []
    for result in pages:
        if result:
            route, html, _ = result
            site.write_if_changed(site.url_file(route), html, changed)
    print(f"SEC landing pages changed: {len(changed)}")


if __name__ == '__main__':
    main()
