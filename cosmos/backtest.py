"""Walk-forward backtest for the market-gravity map ("cosmos").

The map draws sectors as galaxies, the common market factor as a black hole
and a big company-specific move as a supernova whose shock runs along strong
correlations. This script checks, on real daily prices, which of the things
the map could claim about the future actually held in the past:

  T1  Risk forecast      Does a fast volatility estimate (EWMA, lambda 0.94)
                         forecast the next 20 days' volatility better than a
                         plain 250-day average?
  T2  Stress test        "If company A moves X% today, B moves about Y%":
                         are the 80% ranges right 80% of the time, for single
                         stocks and for 15-stock portfolios?
  T3  Delayed gravity    After a large company-specific move in A, do the
                         stocks that usually move with A keep drifting the
                         same way over the next days (a lead-lag effect),
                         beyond a placebo of unrelated stocks?
  T4  Fragility          Does the black hole's mass (lambda1/N, the share of
                         variance in the first principal component) warn of
                         higher volatility or deeper drawdowns ahead?

Every estimate made for day t uses only returns up to day t-1 (or the event
day itself where the test is "given A moved today"), so nothing is fitted on
the outcome it is judged against.

Input: cosmos/data/prices.csv (date,symbol,adj_close[,volume]) written by
fetch_prices.py, or the synthetic demo panel (--synthetic).
Output: cosmos/out/results.json and cosmos/out/results.md.

Usage:
  python cosmos/backtest.py                 # real prices
  python cosmos/backtest.py --synthetic     # demo on generated data
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PRICES = HERE / "data" / "prices.csv"
UNIVERSE = HERE / "data" / "universe.csv"
OUT = HERE / "out"

# Masses for the stress test when the price file carries no volume.
MEGACAPS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK.B", "JPM",
            "LLY", "V", "XOM", "UNH", "MA", "COST", "HD", "PG", "JNJ", "ORCL"]
Z80 = 1.2815515655446004  # two-sided 80% normal interval half-width, in sigmas
COST_BP = 10.0            # rough round-trip trading cost an effect must beat, basis points


@dataclass
class Panel:
    dates: list[str]
    symbols: list[str]
    returns: np.ndarray                      # T x N simple daily returns (0.01 = 1%)
    dollar_volume: np.ndarray | None = None  # N, average close * volume
    sectors: list[str] | None = None         # N, official sector labels when known
    info: dict = field(default_factory=dict)


# ---------------------------------------------------------------- data

def load_prices(path: Path = PRICES, min_coverage: float = 0.95, universe: Path = UNIVERSE) -> Panel:
    """Daily returns for the symbols that have a price on at least `min_coverage` of the dates.

    Gaps of up to three sessions are carried forward (a missing print is a zero
    return that day); longer gaps drop the symbol through the coverage rule.
    """
    prices: dict[str, dict[str, float]] = {}
    volume: dict[str, list[float]] = {}
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            close = float(row["adj_close"]) if row.get("adj_close") else math.nan
            if not close > 0:
                continue
            prices.setdefault(row["symbol"], {})[row["date"]] = close
            if row.get("volume"):
                volume.setdefault(row["symbol"], []).append(close * float(row["volume"]))
    dates = sorted({d for series in prices.values() for d in series})
    keep = [s for s in sorted(prices) if len(prices[s]) >= min_coverage * len(dates)]
    if len(keep) < 20:
        raise SystemExit(f"Only {len(keep)} symbols cover {min_coverage:.0%} of {len(dates)} dates; fetch more history first.")
    P = np.full((len(dates), len(keep)), np.nan)
    index = {d: i for i, d in enumerate(dates)}
    for j, s in enumerate(keep):
        for d, close in prices[s].items():
            P[index[d], j] = close
    for t in range(1, len(dates)):          # carry forward short gaps
        gap = np.isnan(P[t])
        if gap.any():
            P[t, gap] = P[t - 1, gap]
    for t in range(len(dates) - 1, 0, -1):  # and back-fill a symbol's first days
        gap = np.isnan(P[t - 1])
        if gap.any():
            P[t - 1, gap] = P[t, gap]
    R = P[1:] / P[:-1] - 1.0
    R[~np.isfinite(R)] = 0.0
    dv = None
    if volume and all(s in volume for s in keep):
        dv = np.array([float(np.mean(volume[s])) for s in keep])
    sectors = None
    if universe.exists():
        with open(universe, newline="") as handle:
            label = {row["symbol"]: row.get("sector", "") for row in csv.DictReader(handle)}
        sectors = [label.get(s, "") for s in keep]
    return Panel(dates[1:], keep, R, dv, sectors, {"source": str(path), "dropped_for_coverage": len(prices) - len(keep)})


def synthetic_panel(n_stocks: int = 80, n_days: int = 600, n_sectors: int = 8, seed: int = 0,
                    garch: bool = True, jump_prob: float = 0.0, lead_lag: float = 0.0, lag_days: int = 3) -> Panel:
    """A market with a common factor, sector factors and company noise.

    garch     volatility clustering in every component (persistence 0.98)
    jump_prob chance per stock-day of a company-specific jump of 4-7 sigmas
    lead_lag  share of each jump that the other stocks of its sector pick up,
              spread evenly over the next `lag_days` sessions (the planted
              "delayed gravity" effect T3 should find)
    """
    rng = np.random.default_rng(seed)
    sectors = np.arange(n_stocks) % n_sectors
    idio_vol = rng.uniform(0.008, 0.03, n_stocks)

    def path(k: int, vol: float | np.ndarray) -> np.ndarray:
        z = rng.standard_normal((n_days, k))
        if not garch:
            return z * vol
        a, b = 0.08, 0.90
        s2 = np.ones(k)
        out = np.empty((n_days, k))
        for t in range(n_days):
            x = np.sqrt(s2) * z[t]
            out[t] = x
            s2 = (1 - a - b) + a * x * x + b * s2
        return out * vol

    f = path(1, 0.009)[:, 0]
    S = path(n_sectors, 0.007)
    E = path(n_stocks, idio_vol)
    beta = rng.uniform(0.6, 1.4, n_stocks)
    gamma = rng.uniform(0.6, 1.4, n_stocks)
    R = f[:, None] * beta + S[:, sectors] * gamma + E
    J = np.zeros_like(R)
    if jump_prob:
        hit = rng.random(R.shape) < jump_prob
        J = hit * rng.choice([-1.0, 1.0], R.shape) * rng.uniform(4, 7, R.shape) * idio_vol
        R += J
    if lead_lag:
        sector_sum = np.stack([J[:, sectors == s].sum(axis=1) for s in range(n_sectors)], axis=1)
        peer_jumps = sector_sum[:, sectors] - J      # jumps of the other stocks in one's sector
        for k in range(1, lag_days + 1):
            R[k:] += lead_lag / lag_days * peer_jumps[:-k]
    start = date(2023, 1, 2)
    days, d = [], start
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return Panel(days, [f"S{i:03d}" for i in range(n_stocks)], R, None, [f"sector-{s}" for s in sectors],
                 {"source": "synthetic", "seed": seed, "garch": garch, "jump_prob": jump_prob, "lead_lag": lead_lag})


# ---------------------------------------------------------------- helpers

def spearman(x: np.ndarray, y: np.ndarray) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return math.nan
    rx = np.argsort(np.argsort(x[ok])).astype(float)
    ry = np.argsort(np.argsort(y[ok])).astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])


def tstat(values: np.ndarray) -> tuple[float, float, int]:
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if len(v) < 3:
        return math.nan, math.nan, len(v)
    sd = v.std(ddof=1)
    return float(v.mean()), float(v.mean() / (sd / math.sqrt(len(v)))) if sd > 0 else math.nan, len(v)


def ewma_variance(X: np.ndarray, lam: float = 0.94, init: int = 20) -> np.ndarray:
    """Row t = EWMA variance using returns up to and including day t."""
    out = np.empty_like(X)
    prev = np.mean(X[:init] ** 2, axis=0)
    for t in range(X.shape[0]):
        prev = lam * prev + (1 - lam) * X[t] ** 2
        out[t] = prev
    return out


def ols_r2(y: np.ndarray, *xs: np.ndarray) -> float:
    A = np.column_stack([np.ones(len(y)), *xs])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ coef
    return float(1 - resid @ resid / ((y - y.mean()) @ (y - y.mean())))


def r(x: float, digits: int = 4) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), digits)


# ---------------------------------------------------------------- T1

def test_risk(panel: Panel, lam: float = 0.94, horizon: int = 20, base_window: int = 250, step: int = 20) -> dict:
    """EWMA volatility vs the trailing 250-day average, judged on the next `horizon` days.

    Two forecasts are scored, both fixed in advance: plain EWMA, and EWMA
    pulled halfway back to the 250-day level (volatility mean-reverts, and a
    single earnings jump otherwise inflates EWMA for weeks).
    """
    R = panel.returns
    m = R.mean(axis=1, keepdims=True)
    X = np.hstack([R, m])                       # the last column is the market proxy
    T = X.shape[0]
    ew = ewma_variance(X, lam)
    c = np.vstack([np.zeros((1, X.shape[1])), np.cumsum(X ** 2, axis=0)])
    points = list(range(base_window - 1, T - horizon, step))
    if not points:
        return {"verdict": "inconclusive", "why": "too few days", "n": 0}
    t_idx = np.array(points)
    base = (c[t_idx + 1] - c[t_idx + 1 - base_window]) / base_window   # days t-249 .. t
    real = (c[t_idx + 1 + horizon] - c[t_idx + 1]) / horizon           # days t+1 .. t+horizon
    models = {"ewma": ew[t_idx], "ewma_blend": 0.5 * ew[t_idx] + 0.5 * base}

    def score(f: np.ndarray, cols: slice) -> dict:
        f, b, y = f[:, cols].ravel(), base[:, cols].ravel(), real[:, cols].ravel()
        ok = (f > 0) & (b > 0) & (y > 0)
        f, b, y = f[ok], b[ok], y[ok]
        q_model = float(np.mean(y / f - np.log(y / f) - 1))
        q_base = float(np.mean(y / b - np.log(y / b) - 1))
        mse_model, mse_base = float(np.mean((y - f) ** 2)), float(np.mean((y - b) ** 2))
        return {"qlike_ratio": r(q_model / q_base), "mse_ratio": r(mse_model / mse_base),
                "spearman_model": r(spearman(np.sqrt(f), np.sqrt(y))), "spearman_baseline": r(spearman(np.sqrt(b), np.sqrt(y))),
                "n": int(ok.sum())}

    stocks = slice(0, X.shape[1] - 1)
    market = slice(X.shape[1] - 1, X.shape[1])
    table = {name: {"stocks": score(f, stocks), "market": score(f, market)} for name, f in models.items()}

    def passes(sc: dict) -> bool:
        return sc["qlike_ratio"] is not None and sc["qlike_ratio"] < 0.95 and (sc["spearman_model"] or 0) > 0.5

    best = min(models, key=lambda k: table[k]["stocks"]["qlike_ratio"] or math.inf)
    return {"verdict": "works" if passes(table[best]["stocks"]) else "does not work",
            "rule": "works if, for the better of two forecasts fixed in advance (EWMA 0.94; EWMA blended 50/50 with the 250-day level), "
                    "QLIKE(model)/QLIKE(250-day baseline) < 0.95 and Spearman(forecast, realized) > 0.5, pooled over stocks",
            "best_model": best, "stocks": table[best]["stocks"], "market": table[best]["market"], "models": table,
            "forecast_points": len(points), "horizon_days": horizon}


# ---------------------------------------------------------------- T2

def pick_masses(panel: Panel, count: int = 20) -> list[int]:
    if panel.dollar_volume is not None:
        return [int(i) for i in np.argsort(-panel.dollar_volume)[:count]]
    found = [panel.symbols.index(s) for s in MEGACAPS if s in panel.symbols]
    if len(found) >= 5:
        return found
    m = panel.returns[:250].mean(axis=1)
    corr = [np.corrcoef(panel.returns[:250, j], m)[0, 1] for j in range(len(panel.symbols))]
    return [int(i) for i in np.argsort(corr)[::-1][:count]]


def test_stress(panel: Panel, masses: list[int] | None = None, sigma_window: int = 120, fit_window: int = 250,
                k_shock: float = 2.5, n_portfolios: int = 200, portfolio_size: int = 15, seed: int = 7) -> dict:
    """On days a mass moves >= 2.5 sigma, predict every other stock's and portfolio's same-day move."""
    R = panel.returns
    T, N = R.shape
    masses = pick_masses(panel) if masses is None else list(masses)
    rng = np.random.default_rng(seed)
    pool = [j for j in range(N) if j not in set(masses)]
    size = min(portfolio_size, len(pool))
    picks = [rng.choice(pool, size, replace=False) for _ in range(n_portfolios)]
    Pf = np.stack([R[:, p].mean(axis=1) for p in picks], axis=1)
    Y = np.hstack([R, Pf])
    ew_sd = np.sqrt(ewma_variance(Y))
    variants = ("ols", "downside", "shrunk_scaled")
    keep = {v: {"stock": [[], [], []], "portfolio": [[], [], []]} for v in variants}  # pred, real, window mean
    covered = {v: {"stock": [], "portfolio": []} for v in variants}
    events = 0
    for a in masses:
        for t in range(max(fit_window, sigma_window), T):
            sig = R[t - sigma_window:t, a].std(ddof=1)
            shock = R[t, a]
            if sig <= 0 or abs(shock) < k_shock * sig:
                continue
            events += 1
            x, W = R[t - fit_window:t, a], Y[t - fit_window:t]
            groups = (np.arange(Y.shape[1]) < N) & (np.arange(Y.shape[1]) != a), np.arange(Y.shape[1]) >= N
            fits = {}
            for name, rows in (("ols", slice(None)), ("downside", x < 0)):
                xs, Ws = x[rows], W[rows]
                if len(xs) < 30:
                    xs, Ws = x, W
                xc = xs - xs.mean()
                b = xc @ (Ws - Ws.mean(axis=0)) / (xc @ xc)
                a0 = Ws.mean(axis=0) - b * xs.mean()
                res = Ws - a0 - np.outer(xs, b)
                sd = np.sqrt((res ** 2).sum(axis=0) / max(len(xs) - 2, 1))
                fits[name] = (a0, b, sd, xc @ xc)
            if shock >= 0:
                fits["downside"] = fits["ols"]
            # Shrunk and scaled: each beta pulled toward the average beta of its group in
            # proportion to its sampling noise (Vasicek), the residual sigma scaled by recent
            # volatility, and the interval widened by the beta's remaining uncertainty.
            a0, b, sd, sxx = fits["ols"]
            noise = sd ** 2 / sxx
            b_shr, post = b.copy(), np.zeros_like(b)
            for g in groups:
                if g.sum() < 3:
                    continue
                spread = max(float(np.var(b[g]) - np.mean(noise[g])), 1e-12)
                w = spread / (spread + noise[g])
                b_shr[g] = w * b[g] + (1 - w) * float(np.mean(b[g]))
                post[g] = w * noise[g]
            trail_sd = W.std(axis=0, ddof=1)
            scale = np.where(trail_sd > 0, ew_sd[t - 1] / np.where(trail_sd > 0, trail_sd, 1), 1.0)
            fits["shrunk_scaled"] = (W.mean(axis=0) - b_shr * x.mean(), b_shr, np.sqrt((sd * scale) ** 2 + shock ** 2 * post), sxx)
            y = Y[t]
            for name, (a0, b, sd, _) in fits.items():
                pred = a0 + b * shock
                hit = np.abs(y - pred) <= Z80 * sd
                for kind, sel in zip(("stock", "portfolio"), groups):
                    covered[name][kind].append(hit[sel])
                    keep[name][kind][0].append(pred[sel])
                    keep[name][kind][1].append(y[sel])
                    keep[name][kind][2].append(W.mean(axis=0)[sel])

    def summary(name: str, kind: str) -> dict:
        if not covered[name][kind]:
            return {"coverage": None, "slope": None, "r2_oos": None, "n_pairs": 0}
        hit = np.concatenate(covered[name][kind])
        pred, real, mean = (np.concatenate(v) for v in keep[name][kind])
        slope = np.cov(real, pred)[0, 1] / np.var(pred, ddof=1)
        r2 = 1 - np.sum((real - pred) ** 2) / np.sum((real - mean) ** 2)
        return {"coverage": r(hit.mean()), "slope": r(slope), "r2_oos": r(r2), "n_pairs": int(hit.size)}

    table = {name: {kind: summary(name, kind) for kind in ("stock", "portfolio")} for name in variants}

    def distance(s: dict) -> float:
        if s["coverage"] is None:
            return math.inf
        return abs(s["coverage"] - 0.80) / 0.05 + abs(s["slope"] - 1.0) / 0.2

    def in_band(s: dict) -> bool:
        return s["coverage"] is not None and 0.75 <= s["coverage"] <= 0.85 and 0.8 <= s["slope"] <= 1.2

    best = min(variants, key=lambda v: distance(table[v]["stock"]))
    best_pf = min(variants, key=lambda v: distance(table[v]["portfolio"]))
    if events < 30:
        verdict = "inconclusive"
    else:
        verdict = "works" if in_band(table[best]["stock"]) else "does not work"
    return {"verdict": verdict,
            "rule": "works if, for the best of three predictors fixed in advance (OLS beta; downside beta for falls; shrunk beta with volatility-scaled ranges), "
                    "80% ranges cover 75-85% of single-stock moves and the realized/predicted slope is 0.8-1.2",
            "best_variant": best, "portfolio_best_variant": best_pf,
            "portfolio_verdict": ("works" if in_band(table[best_pf]["portfolio"]) else "does not work") if events >= 30 else "inconclusive",
            "variants": table, "shock_events": events, "masses": [panel.symbols[i] for i in masses],
            "portfolios": n_portfolios, "portfolio_size": size}


# ---------------------------------------------------------------- T3

def test_lead_lag(panel: Panel, fit_window: int = 250, sigma_window: int = 120, k_event: float = 3.0, n_peers: int = 10,
                  refit_every: int = 20, seed: int = 11, news_days: dict[str, set[str]] | None = None) -> dict:
    """Signed market-adjusted drift of A's usual companions after a large company-specific move in A.

    news_days (optional): symbol -> dates with an important headline about it;
    when given, only events on those dates count.
    """
    R = panel.returns
    T, N = R.shape
    m = R.mean(axis=1)
    rng = np.random.default_rng(seed)
    horizons = {"day0": (0, 0), "car_1_5": (1, 5), "car_1_20": (1, 20)}
    rows = []          # (t, signed values per horizon for peers, then for placebo)
    same_sector = []
    for d in range(fit_window, T, refit_every):
        mw, Wr = m[d - fit_window:d], R[d - fit_window:d]
        mc = mw - mw.mean()
        beta = mc @ (Wr - Wr.mean(axis=0)) / (mc @ mc)
        alpha = Wr.mean(axis=0) - beta * mw.mean()
        resid = Wr - alpha - np.outer(mw, beta)
        C = np.corrcoef(resid.T) if N > 1 else np.ones((1, 1))
        C[~np.isfinite(C)] = 0.0
        np.fill_diagonal(C, -np.inf)
        lo, hi = d - sigma_window, min(T, d + refit_every + 21)
        E = R[lo:hi] - alpha - np.outer(m[lo:hi], beta)        # residuals with this refit's (past-only) parameters
        for t in range(d, min(d + refit_every, T)):
            i = t - lo
            s = E[i - sigma_window:i].std(axis=0, ddof=1)
            hits = np.where((s > 0) & (np.abs(E[i]) > k_event * s))[0]
            for a in hits:
                if news_days is not None and panel.dates[t] not in news_days.get(panel.symbols[a], ()):
                    continue
                if t + 5 >= T:
                    continue
                sign = 1.0 if E[i, a] > 0 else -1.0
                order = np.argsort(-C[a])
                peers = order[:n_peers]
                # The equal-weight market contains A's cluster: if the cluster drifts, the
                # market drifts with it and every other stock's residual drifts the other
                # way. Outcomes are measured against the market without A's cluster.
                cluster = np.append(order[:max(n_peers, min(50, N // 3))], a)
                others = np.setdiff1d(np.arange(N), cluster)
                placebo = rng.choice(others, min(n_peers, len(others)), replace=False)
                span = slice(t, min(T, t + 21))
                m_ex = (m[span] * N - R[span][:, cluster].sum(axis=1)) / (N - len(cluster))
                vals = []
                for group in (peers, placebo):
                    out = R[span][:, group] - alpha[group] - np.outer(m_ex, beta[group])
                    for h0, h1 in horizons.values():
                        if h1 >= out.shape[0]:
                            vals.append(math.nan)
                        else:
                            vals.append(sign * float(out[h0:h1 + 1].sum(axis=0).mean()))
                rows.append((t, vals))
                if panel.sectors:
                    same_sector.append(np.mean([panel.sectors[p] == panel.sectors[a] for p in peers]))
    if not rows:
        return {"verdict": "inconclusive", "why": "no events", "events": 0}
    names = list(horizons)
    by_date: dict[int, list[list[float]]] = {}
    for t, vals in rows:
        by_date.setdefault(t, []).append(vals)
    dates = sorted(by_date)
    with warnings.catch_warnings():  # a late date can lack the 20-day window: its mean is NaN, then left out
        warnings.simplefilter("ignore", RuntimeWarning)
        per_date = np.array([np.nanmean(np.array(by_date[t]), axis=0) for t in dates])   # date clustering

    def block(sel: np.ndarray) -> dict:
        out = {}
        for k, name in enumerate(names):
            mean, t, n = tstat(per_date[sel, k])
            pmean, pt, _ = tstat(per_date[sel, k + len(names)])
            out[name] = {"mean_bp": r(mean * 1e4, 2), "t": r(t, 2), "placebo_mean_bp": r(pmean * 1e4, 2), "placebo_t": r(pt, 2), "dates": n}
        return out

    every = np.ones(len(dates), bool)
    half = np.arange(len(dates)) < len(dates) // 2
    result = block(every)
    main = result["car_1_5"]
    t_main, effect = main["t"] or 0.0, main["mean_bp"] or 0.0
    placebo_ok = main["placebo_t"] is not None and abs(main["placebo_t"]) < 2
    if len(dates) < 30:
        verdict = "inconclusive"
    elif effect > 0 and t_main > 2 and placebo_ok and effect > COST_BP:
        verdict = "works"
    elif effect > 0 and 1 < t_main <= 2:
        verdict = "inconclusive"
    else:
        verdict = "does not work"
    return {"verdict": verdict,
            "rule": f"works if signed CAR(+1..+5) of A's 10 closest peers is > 0 with t > 2 (dates clustered), placebo |t| < 2 and the mean beats {COST_BP:.0f} bp",
            "events": len(rows), "event_dates": len(dates), "all": result,
            "first_half": block(half), "second_half": block(~half),
            "peers_from_same_official_sector": r(float(np.mean(same_sector)), 3) if same_sector else None,
            "news_filter": news_days is not None}


# ---------------------------------------------------------------- T4

def test_fragility(panel: Panel, window: int = 120, horizon: int = 20, min_blocks: int = 30) -> dict:
    """lambda1/N and the effective number of bets vs the market proxy's next 20 days."""
    R = panel.returns
    T, N = R.shape
    m = R.mean(axis=1)
    out = {"date": [], "lambda1_share": [], "effective_bets": [], "trailing_vol": [], "future_vol": [], "future_max_drawdown": []}
    for t in range(window, T - horizon + 1, horizon):
        X = R[t - window:t]
        live = X.std(axis=0) > 0
        C = np.corrcoef(X[:, live].T)
        n = C.shape[0]
        lam1 = float(np.linalg.eigvalsh(C)[-1])
        path = np.cumprod(1 + m[t:t + horizon])
        peak = np.maximum.accumulate(np.concatenate([[1.0], path]))[1:]
        out["date"].append(panel.dates[t])
        out["lambda1_share"].append(lam1 / n)
        out["effective_bets"].append(n * n / float((C ** 2).sum()))
        out["trailing_vol"].append(float(m[t - horizon:t].std(ddof=1)))
        out["future_vol"].append(float(m[t:t + horizon].std(ddof=1)))
        out["future_max_drawdown"].append(float(1 - np.min(path / peak)))
    lam, trail = np.array(out["lambda1_share"]), np.array(out["trailing_vol"])
    fv, dd = np.array(out["future_vol"]), np.array(out["future_max_drawdown"])
    n_blocks = len(lam)
    stats = {"blocks": n_blocks}
    if n_blocks >= 5:
        stats.update({
            "spearman_lambda_future_vol": r(spearman(lam, fv)), "spearman_lambda_future_drawdown": r(spearman(lam, dd)),
            "spearman_trailing_vol_future_vol": r(spearman(trail, fv)),
            "delta_r2_vol": r(ols_r2(fv, trail, lam) - ols_r2(fv, trail)),
            "delta_r2_drawdown": r(ols_r2(dd, trail, lam) - ols_r2(dd, trail)),
        })
    if n_blocks < min_blocks:
        verdict = "inconclusive"
        why = f"only {n_blocks} independent 20-day blocks; at least {min_blocks} are needed to tell a real signal from luck"
    elif (stats.get("delta_r2_vol") or 0) > 0.05 and (stats.get("spearman_lambda_future_vol") or 0) > 0.3:
        verdict, why = "works", "lambda1/N adds to what trailing volatility already says"
    else:
        verdict, why = "does not work", "lambda1/N adds little beyond trailing volatility"
    series = {k: [r(v, 5) if isinstance(v, float) else v for v in vals] for k, vals in out.items()}
    return {"verdict": verdict, "why": why,
            "rule": "works if lambda1/N adds > 0.05 R^2 to trailing 20-day volatility in forecasting the next 20 days' volatility, with Spearman > 0.3, on >= 30 blocks",
            **stats, "series": series}


# ---------------------------------------------------------------- run + report

def run_all(panel: Panel, masses: list[int] | None = None, news_days: dict[str, set[str]] | None = None) -> dict:
    return {
        "panel": {"symbols": len(panel.symbols), "days": len(panel.dates),
                  "first": panel.dates[0], "last": panel.dates[-1], **panel.info},
        "T1_risk_forecast": test_risk(panel),
        "T2_stress_test": test_stress(panel, masses),
        "T3_delayed_gravity": test_lead_lag(panel, news_days=news_days),
        "T4_fragility": test_fragility(panel),
    }


def markdown(res: dict) -> str:
    p = res["panel"]
    t1, t2, t3, t4 = (res[k] for k in ("T1_risk_forecast", "T2_stress_test", "T3_delayed_gravity", "T4_fragility"))
    lines = [
        "# Cosmos backtest results", "",
        f"Data: {p['symbols']} symbols, {p['days']} trading days ({p['first']} to {p['last']}), source {p.get('source')}.",
        "Every estimate for a day uses only earlier data. These are results on history, not forecasts.", "",
        "| Test | Verdict | Key numbers |", "| --- | --- | --- |",
        f"| T1 Risk forecast | {t1['verdict']} | QLIKE ratio {t1['stocks']['qlike_ratio']}, Spearman {t1['stocks']['spearman_model']} (baseline {t1['stocks']['spearman_baseline']}) |",
    ]
    if t2.get("variants"):
        b = t2["variants"][t2["best_variant"]]["stock"]
        pf = t2["variants"][t2["portfolio_best_variant"]]["portfolio"]
        lines.append(f"| T2 Stress test | {t2['verdict']} (portfolios: {t2['portfolio_verdict']}) | stocks: coverage {b['coverage']}, slope {b['slope']} ({t2['best_variant']}); portfolios: coverage {pf['coverage']}, slope {pf['slope']} ({t2['portfolio_best_variant']}); {t2['shock_events']} shocks |")
    if t3.get("all"):
        c = t3["all"]["car_1_5"]
        lines.append(f"| T3 Delayed gravity | {t3['verdict']} | CAR(+1..+5) {c['mean_bp']} bp, t {c['t']}; placebo {c['placebo_mean_bp']} bp, t {c['placebo_t']}; {t3['event_dates']} event dates |")
    lines.append(f"| T4 Fragility | {t4['verdict']} | {t4['blocks']} blocks; Spearman(lambda, future vol) {t4.get('spearman_lambda_future_vol')}, extra R^2 {t4.get('delta_r2_vol')} |")
    lines += ["", "## Rules", ""]
    for name, t in (("T1", t1), ("T2", t2), ("T3", t3), ("T4", t4)):
        if t.get("rule"):
            lines.append(f"- {name}: {t['rule']}.")
    if t3.get("all"):
        lines += ["", "## T3 by horizon", "", "| Window | Peers, bp | t | Placebo, bp | Placebo t |", "| --- | --- | --- | --- | --- |"]
        for label, part in (("all", t3["all"]), ("first half", t3["first_half"]), ("second half", t3["second_half"])):
            for h, v in part.items():
                lines.append(f"| {label} {h} | {v['mean_bp']} | {v['t']} | {v['placebo_mean_bp']} | {v['placebo_t']} |")
    if t4.get("why"):
        lines += ["", f"T4 note: {t4['why']}."]
    return "\n".join(lines) + "\n"


def write_reports(res: dict, out_dir: Path = OUT) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    (out_dir / "results.md").write_text(markdown(res))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prices", type=Path, default=PRICES)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--synthetic", action="store_true", help="run on a generated market with a planted lead-lag effect")
    args = ap.parse_args(argv)
    if args.synthetic:
        panel = synthetic_panel(n_stocks=120, n_days=750, jump_prob=0.02, lead_lag=0.3, seed=1)
        masses = list(range(20))
    else:
        if not args.prices.exists():
            print(f"{args.prices} not found: run fetch_prices.py first (needs MARKETSTACK_API_KEY)", file=sys.stderr)
            return 2
        panel, masses = load_prices(args.prices), None
    res = run_all(panel, masses)
    write_reports(res, args.out)
    print(markdown(res))
    print(f"Written to {args.out}/results.json and results.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
