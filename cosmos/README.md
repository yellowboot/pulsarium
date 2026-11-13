# Cosmos backtest

The cosmos map draws the market as space: sectors are galaxies, stocks are
stars, the common market factor is a black hole whose mass is lambda1/N (the
share of all variance in the first principal component), and a big
company-specific move is a supernova whose shock runs along strong
correlations. Before the map makes any claim about what comes next, this
backtest checks on real prices which of those claims held in the past.

Every estimate for day t uses only returns up to day t-1, or the event day
itself where the question is "given A moved today". Nothing is fitted on the
outcome it is judged against.

## The four tests

| Test | Question | Verdict "works" when |
| --- | --- | --- |
| T1 Risk forecast | Does a fast volatility estimate forecast the next 20 sessions' volatility better than the 250-day average? | For the better of two forecasts fixed in advance (EWMA 0.94; EWMA blended 50/50 with the 250-day level), QLIKE ratio < 0.95 and Spearman(forecast, realized) > 0.5, pooled over stocks |
| T2 Stress test | When one of 20 large companies moves at least 2.5 sigma, are "B moves about Y%" predictions right? | For the best of three predictors fixed in advance (OLS beta; downside beta for falls; shrunk beta with volatility-scaled ranges), 80% ranges cover 75-85% of single-stock moves and realized/predicted slope is 0.8-1.2. The same is reported for 200 random 15-stock portfolios |
| T3 Delayed gravity | After a market-adjusted move above 3 sigma in A, do A's 10 closest stocks keep drifting the same way over the next 5 sessions? | Signed CAR(+1..+5) > 0 with t > 2 (event dates clustered), a placebo of 10 unrelated stocks has abs(t) < 2, and the mean beats 10 bp of trading cost. Day 0 and +1..+20 are reported too, and both halves of the sample to show decay |
| T4 Fragility | Does lambda1/N (and the effective number of bets, N^2 / sum of squared correlations) warn of higher volatility or deeper drawdowns over the next 20 sessions? | lambda1/N adds more than 0.05 R^2 to trailing 20-day volatility, with Spearman > 0.3, on at least 30 non-overlapping 20-day blocks |

In T3, outcomes are measured against an equal-weight market that leaves out
A and its 50 closest stocks: otherwise a drifting cluster drags the market
with it and every unrelated stock looks like it drifts the other way.

## Run

```
pip install -r cosmos/requirements.txt
MARKETSTACK_API_KEY=... python cosmos/fetch_prices.py      # about 630 requests on the first run
python cosmos/backtest.py                                  # writes cosmos/out/results.json and results.md
python cosmos/backtest.py --synthetic                      # the same report on a generated market
python -m unittest discover -s cosmos -p "test_*.py"       # about 2 seconds
```

`fetch_prices.py` asks for one symbol per request (three years fit in one
1000-row answer), so the cost is about one request per followed symbol,
whether Marketstack counts a multi-symbol call as one request or as one per
symbol. Raw answers are kept in `cosmos/data/raw/`; a re-run only asks for
symbols not saved yet. `--max-requests` caps a run (default 700).

`cosmos/data/` and `cosmos/out/` are ignored by git: the repository is public
and raw prices must not be republished.

## What the results can and cannot say

- They describe history. A test that "works" held over the sample; it can stop
  working, and published market effects usually weaken once known.
- Moving together is not causing. T3 comes closest to direction (a move in A,
  then drift in B), but a shared cause can still drive both.
- T4 has little power: three years give about 35 independent 20-day blocks,
  barely above the minimum. Treat its verdict as a first look.
- T2 judges single company shocks against betas from ordinary days. A
  company's own news usually spreads less than its beta implies, so the
  slope check is where a naive "if A falls 10%" calculator fails.
- The news archive (`data/archive/`) is not used yet: its items carry no
  ticker and start on 2026-08-12. `test_lead_lag(news_days=...)` already takes
  a symbol -> dates map for when headlines are matched to tickers.
