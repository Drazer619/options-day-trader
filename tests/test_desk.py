"""Offline tests: no network. Run: python -m unittest discover -s tests -v"""
import datetime
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1] / "skills" / "options-day-trader" / "desk.py"
TMP = tempfile.mkdtemp(prefix="desk-test-")
os.environ["OPTIONS_DESK_HOME"] = TMP

spec = importlib.util.spec_from_file_location("desk", DESK)
desk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desk)


def run(*args):
    env = {**os.environ, "OPTIONS_DESK_HOME": TMP, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(DESK), *args], capture_output=True, text=True, env=env)


SETUP = ["setup", "--capital", "100000", "--max-loss-per-trade", "2000", "--max-loss-per-day", "4000",
         "--max-trades-per-day", "2", "--max-lots", "2", "--underlyings", "nifty,sensex", "--broker", "zerodha",
         "--style", "buy_only", "--mode", "paper", "--experience", "new"]


class Pricing(unittest.TestCase):
    def test_put_call_parity(self):
        s, k, t, iv = 25000, 25100, 5 / 365, 0.15
        c, p = desk.bs(s, k, t, iv, "CE")["price"], desk.bs(s, k, t, iv, "PE")["price"]
        self.assertAlmostEqual(c - p, s - k * math.exp(-desk.RISK_FREE * t), delta=0.05)

    def test_deltas_and_theta(self):
        ce, pe = desk.bs(25000, 25000, 3 / 365, 0.15, "CE"), desk.bs(25000, 25000, 3 / 365, 0.15, "PE")
        self.assertTrue(0.4 < ce["delta"] < 0.6 and -0.6 < pe["delta"] < -0.4)
        self.assertLess(ce["theta_per_day"], 0)

    def test_expiry_floor_no_crash(self):
        self.assertGreaterEqual(desk.bs(25000, 25000, 0, 0.15, "CE")["price"], 0)


class Strikes(unittest.TestCase):
    chain = [{"strike": str(k), "instrument_type": t, "tradingsymbol": f"X{k}{t}"}
             for k in range(24000, 26050, 50) for t in ("CE", "PE")]

    def test_step_atm_symbol(self):
        self.assertEqual(desk.strike_step(self.chain, 25012), 50)
        self.assertEqual(desk.atm_of(self.chain, 25012), 25000)
        self.assertEqual(desk.symbol_for({"seg": "NFO"}, self.chain, 25000.0, "CE"), "NFO:X25000CE")


class SetupAndJournal(unittest.TestCase):
    def setUp(self):
        for f in ("config.json", "journal.csv"):
            (Path(TMP) / f).unlink(missing_ok=True)

    def test_needs_setup_first(self):
        self.assertTrue(json.loads(run("settings").stdout)["needs_setup"])

    def test_setup_missing_fields(self):
        r = run("setup", "--capital", "1000")
        self.assertEqual(r.returncode, 2)
        self.assertIn("max_loss_per_trade_inr", json.loads(r.stdout)["missing"])

    def test_setup_saves_and_partial_update(self):
        self.assertTrue(json.loads(run(*SETUP).stdout)["saved"])
        run("setup", "--max-lots", "1")
        s = json.loads(run("settings").stdout)["settings"]
        self.assertEqual(s["max_lots"], 1)
        self.assertEqual(s["capital_inr"], 100000.0)
        self.assertEqual(s["underlyings"], "NIFTY,SENSEX")

    def test_journal_daily_trade_limit(self):
        run(*SETUP)
        cache = Path(TMP) / "cache"
        cache.mkdir(exist_ok=True)
        today = datetime.date.today()
        # journal add validates the symbol against the cached contract list: give it a tiny offline one
        (cache / f"NFO_{today}.csv").write_text(
            "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange\n"
            f"1,1,NIFTYX25000CE,NIFTY,0,{today},25000,0.05,65,CE,NFO-OPT,NFO\n", encoding="utf-8")
        add = ["journal", "add", "--u", "NIFTY", "--contract", "NFO:T", "--side", "BUY", "--lots", "1",
               "--lot-size", "65", "--entry", "100", "--stop", "80", "--target", "140"]
        self.assertIn("logged", run(*add).stdout)
        self.assertIn("logged", run(*add).stdout)
        self.assertIn("blocked", run(*add).stdout)


if __name__ == "__main__":
    unittest.main()
