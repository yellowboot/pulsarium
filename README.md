# Pulsarium

**A calmer way to watch the market.**

[Pulsarium](https://pulsarium.finance/) is an independent stock and ETF portfolio tracker and financial news platform for individual investors. Headlines are tagged by ticker and sector, with sentiment and importance indicators. A free account adds portfolio tracking, dividends, watchlists, price alerts and broker file import. Users record transactions manually or import broker export files; their investments remain with their chosen broker.

- [Explore Pulsarium](https://pulsarium.finance/)
- [Read market news](https://pulsarium.finance/news/)
- [Open the app](https://app.pulsarium.finance/)

This public repository contains the landing page, news dashboard, and scheduled news feed. The account application is maintained separately.

The public site's Neon and Calm themes share `assets/themes.css` and `assets/theme.js`. The header switch remembers the device's choice in local storage and applies it before the page paints. New pages receive these assets through `site_build.py`; after changing a shared asset, run `python site_theme.py` to refresh all existing pages together. `python site_theme.py --check` verifies that they are up to date. The compact Mood embed keeps its own `?theme=light|dark` setting.

Questions or feedback? Write to [contact@pulsarium.finance](mailto:contact@pulsarium.finance).
