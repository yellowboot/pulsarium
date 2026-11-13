"""The backtest must find effects planted in a synthetic market and must not invent them.

Run: python -m unittest discover -s cosmos -p "test_*.py"
"""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as bt       # noqa: E402
import fetch_prices as fp   # noqa: E402


class RiskForecast(unittest.TestCase):
    def test_clustered_volatility_is_forecast(self):
        # (a) GARCH-like clustering in every component: a fast estimate must beat the 250-day average
        for seed in (0, 1):
            res = bt.test_risk(bt.synthetic_panel(n_stocks=80, n_days=600, seed=seed, garch=True))
            self.assertEqual(res["verdict"], "works", res["stocks"])
            self.assertLess(res["stocks"]["qlike_ratio"], 0.95)

    def test_constant_volatility_gives_no_edge(self):
        # nothing to forecast: the fast estimate must not look better than the slow one
        res = bt.test_risk(bt.synthetic_panel(n_stocks=80, n_days=600, seed=3, garch=False))
        self.assertEqual(res["verdict"], "does not work", res["stocks"])


class StressTest(unittest.TestCase):
    def test_ranges_are_calibrated_in_a_gaussian_factor_market(self):
        # (b) one market factor + sector factors, Gaussian: 80% ranges hold 80% of the time
        res = bt.test_stress(bt.synthetic_panel(n_stocks=100, n_days=900, seed=0, garch=False), masses=list(range(8)))
        self.assertGreaterEqual(res["shock_events"], 30)
        self.assertEqual(res["verdict"], "works", res["variants"])
        best = res["variants"][res["best_variant"]]["stock"]
        self.assertTrue(0.75 <= best["coverage"] <= 0.85, best)
        self.assertTrue(0.8 <= best["slope"] <= 1.2, best)
        pf = res["variants"][res["portfolio_best_variant"]]["portfolio"]
        self.assertTrue(0.75 <= pf["coverage"] <= 0.85, pf)

    def test_company_specific_jumps_do_not_pass_as_market_moves(self):
        # a mass's own jumps do not spread, so a beta that assumes they do must fail the slope check
        res = bt.test_stress(bt.synthetic_panel(n_stocks=100, n_days=900, seed=0, garch=False, jump_prob=0.03), masses=list(range(8)))
        self.assertEqual(res["verdict"], "does not work", res["variants"])


class DelayedGravity(unittest.TestCase):
    def test_planted_lead_lag_is_found(self):
        # (c) sector peers pick up 30% of a company's jump over the next 3 sessions
        res = bt.test_lead_lag(bt.synthetic_panel(n_stocks=80, n_days=600, seed=0, jump_prob=0.02, lead_lag=0.3))
        self.assertEqual(res["verdict"], "works", res["all"])
        self.assertGreater(res["all"]["car_1_5"]["t"], 2)
        self.assertLess(abs(res["all"]["car_1_5"]["placebo_t"]), 2)

    def test_no_lead_lag_is_not_invented(self):
        # (d) same market without the effect: at most one false "works" in five seeds
        verdicts = [bt.test_lead_lag(bt.synthetic_panel(n_stocks=80, n_days=600, seed=seed, jump_prob=0.02))["verdict"]
                    for seed in range(5)]
        self.assertLessEqual(verdicts.count("works"), 1, verdicts)

    def test_news_filter_keeps_only_listed_days(self):
        panel = bt.synthetic_panel(n_stocks=80, n_days=600, seed=0, jump_prob=0.02, lead_lag=0.3)
        res = bt.test_lead_lag(panel, news_days={})
        self.assertEqual(res["events"], 0)


class Fragility(unittest.TestCase):
    def test_runs_and_reports_sane_numbers(self):
        # (e) shares and counts inside their ranges; low power is called inconclusive
        panel = bt.synthetic_panel(n_stocks=80, n_days=600, seed=0)
        res = bt.test_fragility(panel)
        lam, enb = np.array(res["series"]["lambda1_share"]), np.array(res["series"]["effective_bets"])
        self.assertEqual(res["blocks"], len(range(120, 600 - 20 + 1, 20)))
        self.assertTrue(np.all((lam > 0) & (lam <= 1)))
        self.assertTrue(np.all((enb >= 1) & (enb <= 80)))
        self.assertEqual(res["verdict"], "inconclusive")
        # a market where the common factor dominates has fewer independent bets
        tight = bt.test_fragility(bt.synthetic_panel(n_stocks=80, n_days=600, seed=0, garch=False))
        self.assertGreater(np.mean(tight["series"]["effective_bets"]), 1)


class Reports(unittest.TestCase):
    def test_full_run_writes_json_and_markdown(self):
        panel = bt.synthetic_panel(n_stocks=60, n_days=520, seed=2, jump_prob=0.02, lead_lag=0.3)
        res = bt.run_all(panel, masses=list(range(6)))
        with tempfile.TemporaryDirectory() as tmp:
            bt.write_reports(res, Path(tmp))
            data = json.loads((Path(tmp) / "results.json").read_text())
            text = (Path(tmp) / "results.md").read_text()
        for key in ("T1_risk_forecast", "T2_stress_test", "T3_delayed_gravity", "T4_fragility"):
            self.assertIn(data[key]["verdict"], ("works", "does not work", "inconclusive"))
        self.assertIn("| T3 Delayed gravity |", text)

    def test_prices_file_round_trip(self):
        rng = np.random.default_rng(0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.csv"
            with open(path, "w", newline="") as handle:
                out = csv.writer(handle)
                out.writerow(["date", "symbol", "adj_close", "volume"])
                for j in range(25):
                    price = 100.0
                    for t in range(300):
                        price *= 1 + 0.01 * rng.standard_normal()
                        if j == 0 and t == 150:
                            continue                      # one missing print, carried forward
                        out.writerow([f"2024-{1 + t // 28:02d}-{1 + t % 28:02d}", f"T{j:02d}", f"{price:.4f}", "1000"])
            panel = bt.load_prices(path, universe=Path(tmp) / "none.csv")
        self.assertEqual(len(panel.symbols), 25)
        self.assertEqual(panel.returns.shape, (299, 25))
        self.assertTrue(np.isfinite(panel.returns).all())
        self.assertIsNotNone(panel.dollar_volume)


class FetchGuards(unittest.TestCase):
    def test_data_slip_drops_the_symbol(self):
        good = [{"date": f"2026-01-{d:02d}", "adj_close": 100 + d, "volume": 10} for d in range(1, 20)]
        slip = [dict(row) for row in good]
        slip[10]["adj_close"] = 2.0       # a -98% "move" from a share-ratio change the closes missed
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            (raw / "GOOD.json").write_text(json.dumps(good))
            (raw / "SLIP.json").write_text(json.dumps(slip))
            summary = fp.assemble(raw, Path(tmp) / "prices.csv")
            with open(Path(tmp) / "prices.csv", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(summary["symbols"], 1)
        self.assertIn("SLIP", summary["dropped"])
        self.assertEqual({row["symbol"] for row in rows}, {"GOOD"})

    def test_rows_found_in_any_nesting(self):
        rows = [{"symbol": "A", "date": "2026-01-02", "close": 1}]
        self.assertEqual(fp.eod_rows({"eod": rows}), rows)
        self.assertEqual(fp.eod_rows([rows]), rows)


if __name__ == "__main__":
    unittest.main()
