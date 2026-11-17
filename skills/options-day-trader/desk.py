"""Universal Indian index & stock OPTIONS day-trading desk (analysis + paper only).

NEVER places orders. Reads free public data (Yahoo Finance charts, Zerodha's
public instrument list) and prints plans the trader places by hand.

Works for every NSE/BSE option underlying: NIFTY, BANKNIFTY, FINNIFTY,
MIDCPNIFTY, NIFTYNXT50, SENSEX, BANKEX and ~220 F&O stocks (RELIANCE, ...).

First run:   desk.py setup --capital ... --risk-per-trade ...   (see `setup -h`)
Then:        brief | orb | watch | strikes | plan | odds | journal | settings | doctor
Every command takes --u UNDERLYING (default: first in your settings, else NIFTY).
Needs Python 3.10+ and: pip install yfinance pandas numpy requests
Data folder: $OPTIONS_DESK_HOME, else this script's folder.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(os.environ.get("OPTIONS_DESK_HOME") or Path(__file__).resolve().parent)  # settings, journal, runs, cache
ROOT.mkdir(parents=True, exist_ok=True)
CONFIG = ROOT / "config.json"
IST = timezone(timedelta(hours=5, minutes=30))
OPEN, CLOSE = time(9, 15), time(15, 30)
RISK_FREE = 0.065

# Underlying -> (Yahoo symbol, Kite segment). Anything else is treated as an NSE stock.
INDICES = {
    "NIFTY": ("^NSEI", "NFO"), "BANKNIFTY": ("^NSEBANK", "NFO"),
    "FINNIFTY": ("NIFTY_FIN_SERVICE.NS", "NFO"), "MIDCPNIFTY": ("NIFTY_MID_SELECT.NS", "NFO"),
    "NIFTYNXT50": ("^NSMIDCP", "NFO"), "SENSEX": ("^BSESN", "BFO"), "BANKEX": ("BSE-BANK.BO", "BFO"),
}
CUES = {"S&P fut": "ES=F", "Nasdaq fut": "NQ=F", "Brent": "BZ=F", "USD/INR": "INR=X",
        "Nikkei": "^N225", "Hang Seng": "^HSI"}

# What `setup` asks for. key -> (type, help). All required unless a default is shown.
SETTINGS = {
    "capital_inr": (float, "Money set aside for option trading (Rs)"),
    "max_loss_per_trade_inr": (float, "Most you will lose on ONE trade (Rs)"),
    "max_loss_per_day_inr": (float, "Stop trading for the day after losing this much (Rs)"),
    "max_trades_per_day": (int, "Max trades per day"),
    "max_lots": (int, "Max lots in one trade"),
    "underlyings": (str, "Comma list you trade, e.g. NIFTY,SENSEX,BANKNIFTY"),
    "broker": (str, "zerodha / upstox / angelone / dhan / groww / other / none"),
    "style": (str, "buy_only (CE/PE buying) or buy_and_spreads (also debit spreads)"),
    "mode": (str, "paper (practice) or real"),
    "experience": (str, "new / some / experienced"),
}
DEFAULTS = {
    "max_capital_per_trade_pct": 50, "orb_minutes": 15, "no_trade_first_minutes": 15,
    "no_new_entries_after": "14:30", "square_off_by": "15:15", "trade_expiry_day": False,
}


# ---------------------------------------------------------------- helpers
def now_ist() -> datetime:
    return datetime.now(IST)


def load_config() -> dict | None:
    return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8"))} if CONFIG.exists() else None


def need_config() -> dict:
    cfg = load_config()
    if cfg is None:
        print(json.dumps({"needs_setup": True,
                          "message": "No trading settings yet. Ask the trader the setup questions, then run `desk.py setup ...`.",
                          "questions": {k: v[1] for k, v in SETTINGS.items()}}, indent=2))
        sys.exit(2)
    return cfg


def default_underlying() -> str:
    cfg = load_config()
    if cfg and cfg.get("underlyings"):
        return str(cfg["underlyings"]).split(",")[0].strip().upper()
    return "NIFTY"


def meta(u: str) -> dict:
    u = u.upper()
    ysym, seg = INDICES.get(u, (f"{u}.NS", "NFO"))
    return {"name": u, "yf": ysym, "seg": seg, "is_index": u in INDICES}


def run_dir() -> Path:
    d = ROOT / "runs" / now_ist().strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(name: str, data: dict) -> Path:
    p = run_dir() / name
    p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return p


def out(data: dict, fname: str | None = None) -> None:
    if fname:
        save(fname, data)
    print(json.dumps(data, indent=2, default=str))


def bars(sym: str, period: str, interval: str) -> pd.DataFrame:
    df = yf.Ticker(sym).history(period=period, interval=interval)
    if df.empty:
        raise RuntimeError(f"no price data for {sym} ({period}, {interval})")
    if interval.endswith("m"):
        df.index = df.index.tz_convert(IST)
    return df


def spot_of(m: dict) -> float:
    return float(bars(m["yf"], "5d", "5m")["Close"].iloc[-1])


def rsi(close: pd.Series, n: int = 14) -> float:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return float(100 - 100 / (1 + up.iloc[-1] / dn.iloc[-1]))


def atr(df: pd.DataFrame, n: int = 14) -> float:
    pc = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


def hist_vol(m: dict) -> float:
    c = bars(m["yf"], "3mo", "1d")["Close"]
    return float(np.log(c).diff().dropna().tail(20).std() * math.sqrt(252))


def implied_vol(m: dict) -> tuple[float, str]:
    """Rough IV for estimates: India VIX for NIFTY/SENSEX family, else 20-day realised vol x1.1."""
    if m["name"] in ("NIFTY", "SENSEX", "NIFTYNXT50"):
        return float(bars("^INDIAVIX", "5d", "1d")["Close"].iloc[-1]) / 100, "India VIX"
    return hist_vol(m) * 1.1, "20-day realised vol x1.1"


# ------------------------------------------------------------ Black-Scholes
def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(spot: float, strike: float, t_years: float, iv: float, kind: str) -> dict:
    t = max(t_years, 1 / (365 * 24 * 4))
    d1 = (math.log(spot / strike) + (RISK_FREE + iv * iv / 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    pdf = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    disc = strike * math.exp(-RISK_FREE * t)
    if kind == "CE":
        price, delta = spot * _ncdf(d1) - disc * _ncdf(d2), _ncdf(d1)
        theta = -spot * pdf * iv / (2 * math.sqrt(t)) - RISK_FREE * disc * _ncdf(d2)
    else:
        price, delta = disc * _ncdf(-d2) - spot * _ncdf(-d1), _ncdf(d1) - 1
        theta = -spot * pdf * iv / (2 * math.sqrt(t)) + RISK_FREE * disc * _ncdf(-d2)
    return {"price": round(price, 2), "delta": round(delta, 3), "theta_per_day": round(theta / 365, 2),
            "gamma": round(pdf / (spot * iv * math.sqrt(t)), 6)}


# -------------------------------------------------------------- instruments
def instruments(m: dict) -> list[dict]:
    cache = ROOT / "cache" / f"{m['seg']}_{date.today()}.csv"
    cache.parent.mkdir(exist_ok=True)
    if not cache.exists():
        r = requests.get(f"https://api.kite.trade/instruments/{m['seg']}", timeout=60)
        r.raise_for_status()
        cache.write_text(r.text, encoding="utf-8")
    rows = [r for r in csv.DictReader(io.StringIO(cache.read_text(encoding="utf-8")))
            if r["name"] == m["name"] and r["instrument_type"] in ("CE", "PE")]
    if not rows:
        raise SystemExit(json.dumps({"error": f"No options found for '{m['name']}' on {m['seg']}. "
                                              "Use the exact F&O symbol, e.g. NIFTY, BANKNIFTY, SENSEX, RELIANCE."}))
    return rows


def next_expiry(m: dict) -> tuple[date, int, list[dict]]:
    today, rows = now_ist().date(), instruments(m)
    after_close = now_ist().time() >= CLOSE
    exp = min(e for e in {date.fromisoformat(r["expiry"]) for r in rows}
              if e > today or (e == today and not after_close))
    chain = [r for r in rows if r["expiry"] == exp.isoformat()]
    return exp, int(chain[0]["lot_size"]), chain


def strike_step(chain: list[dict], spot: float) -> float:
    ks = sorted({float(r["strike"]) for r in chain})
    near = [k for k in ks if abs(k - spot) / spot < 0.05] or ks
    diffs = [round(b - a, 2) for a, b in zip(near, near[1:])]
    return Counter(diffs).most_common(1)[0][0] if diffs else 1.0


def atm_of(chain: list[dict], spot: float) -> float:
    return min({float(r["strike"]) for r in chain}, key=lambda k: abs(k - spot))


def symbol_for(m: dict, chain: list[dict], strike: float, kind: str) -> str | None:
    for r in chain:
        if float(r["strike"]) == strike and r["instrument_type"] == kind:
            return f"{m['seg']}:{r['tradingsymbol']}"
    return None


def years_to(exp: date) -> float:
    return max((datetime.combine(exp, CLOSE, IST) - now_ist()).total_seconds(), 0) / (365 * 24 * 3600)


def fmt_strike(k: float):
    return int(k) if float(k).is_integer() else k


# ------------------------------------------------------------------ commands
def cmd_setup(a) -> None:
    cfg = load_config() or {}
    cfg = {k: v for k, v in cfg.items() if k not in DEFAULTS or v != DEFAULTS[k]}
    for k, (typ, _h) in SETTINGS.items():
        v = getattr(a, k)
        if v is not None:
            cfg[k] = typ(v)
    for k in DEFAULTS:
        v = getattr(a, k, None)
        if v is not None:
            cfg[k] = v
    missing = [k for k in SETTINGS if k not in cfg]
    if missing:
        out({"saved": False, "missing": {k: SETTINGS[k][1] for k in missing}}); sys.exit(2)
    if "underlyings" in cfg:
        cfg["underlyings"] = ",".join(x.strip().upper() for x in str(cfg["underlyings"]).split(",") if x.strip())
    problems = []
    if cfg["max_loss_per_trade_inr"] > cfg["max_loss_per_day_inr"]:
        problems.append("Per-trade loss is bigger than the daily loss limit.")
    if cfg["max_loss_per_day_inr"] > 0.1 * cfg["capital_inr"]:
        problems.append("Daily loss limit is over 10% of capital - that is aggressive.")
    if cfg["mode"] not in ("paper", "real"):
        problems.append("mode must be paper or real.")
    if cfg["style"] not in ("buy_only", "buy_and_spreads"):
        problems.append("style must be buy_only or buy_and_spreads.")
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    out({"saved": True, "settings": {**DEFAULTS, **cfg}, "warnings": problems})


def cmd_settings(a) -> None:
    cfg = load_config()
    out({"settings": cfg} if cfg else {"needs_setup": True, "questions": {k: v[1] for k, v in SETTINGS.items()}})


def cmd_doctor(a) -> None:
    checks = {}
    for name, sym in [("NIFTY price (Yahoo)", "^NSEI"), ("India VIX (Yahoo)", "^INDIAVIX")]:
        try:
            df = bars(sym, "5d", "5m")
            age = (now_ist() - df.index[-1]).total_seconds() / 60
            checks[name] = f"OK, last bar {df.index[-1]:%d %b %H:%M} ({age:.0f} min ago)"
        except Exception as e:
            checks[name] = f"FAIL: {e}"
    for seg in ("NFO", "BFO"):
        try:
            instruments({"name": "NIFTY" if seg == "NFO" else "SENSEX", "seg": seg})
            checks[f"{seg} contract list (Zerodha public)"] = "OK"
        except BaseException as e:
            checks[f"{seg} contract list (Zerodha public)"] = f"FAIL: {e}"
    cfg = load_config()
    checks["settings"] = "OK" if cfg else "MISSING - run setup"
    t = now_ist().time()
    checks["market"] = "open" if OPEN <= t < CLOSE and now_ist().weekday() < 5 else "closed (holidays not checked)"
    out({"doctor": checks, "time_ist": now_ist().strftime("%a %d %b %H:%M")})


def cmd_brief(a) -> None:
    m = meta(a.u)
    d = bars(m["yf"], "1y", "1d")
    i5 = bars(m["yf"], "5d", "5m")
    close = d["Close"]
    spot = float(i5["Close"].iloc[-1])
    last_day = i5.index[-1].date()
    prev = d[d.index.date < last_day].iloc[-1] if now_ist().time() < CLOSE and last_day == now_ist().date() else d.iloc[-1]
    H, L, C = float(prev["High"]), float(prev["Low"]), float(prev["Close"])
    P = (H + L + C) / 3
    bc, tc = (H + L) / 2, 2 * P - (H + L) / 2
    iv, iv_src = implied_vol(m)
    exp, lot, chain = next_expiry(m)
    T = years_to(exp)
    atm = atm_of(chain, spot)
    step = strike_step(chain, spot)
    move = spot * iv / math.sqrt(252)
    cues = {}
    for k, s in CUES.items():
        try:
            h = bars(s, "5d", "1d")["Close"]
            cues[k] = {"last": round(float(h.iloc[-1]), 2), "chg_pct": round(float(h.iloc[-1] / h.iloc[-2] - 1) * 100, 2)}
        except Exception as e:
            cues[k] = {"error": str(e)[:80]}
    rows = []
    for off in range(-2, 3):
        k = atm + off * step
        ce, pe = bs(spot, k, T, iv, "CE"), bs(spot, k, T, iv, "PE")
        rows.append({"strike": fmt_strike(k), "CE_est": ce["price"], "CE_delta": ce["delta"], "CE_theta": ce["theta_per_day"],
                     "PE_est": pe["price"], "PE_delta": pe["delta"], "PE_theta": pe["theta_per_day"],
                     "CE_symbol": symbol_for(m, chain, k, "CE"), "PE_symbol": symbol_for(m, chain, k, "PE")})
    out({
        "underlying": m["name"], "as_of_ist": now_ist().strftime("%Y-%m-%d %H:%M"), "spot": round(spot, 2),
        "prev_day": {"high": round(H, 2), "low": round(L, 2), "close": round(C, 2)},
        "pivots": {"R2": round(P + (H - L), 1), "R1": round(2 * P - L, 1), "P": round(P, 1),
                   "S1": round(2 * P - H, 1), "S2": round(P - (H - L), 1),
                   "CPR_low": round(min(bc, tc), 1), "CPR_high": round(max(bc, tc), 1)},
        "trend_daily": {"ema10": round(float(close.ewm(span=10).mean().iloc[-1]), 1),
                        "sma50": round(float(close.rolling(50).mean().iloc[-1]), 1),
                        "sma200": round(float(close.rolling(200).mean().iloc[-1]), 1),
                        "rsi14": round(rsi(close), 1), "atr14": round(atr(d), 1),
                        "last5_closes": [round(float(x), 1) for x in close.tail(5)]},
        "volatility": {"iv_used_pct": round(iv * 100, 2), "iv_source": iv_src,
                       "implied_1day_move": round(move, 1), "range_1sigma": [round(spot - move, 1), round(spot + move, 1)]},
        "expiry": {"date": exp.isoformat(), "days_left": round(T * 365, 2), "lot_size": lot,
                   "is_expiry_day": exp == now_ist().date()},
        "atm": fmt_strike(atm), "strike_step": fmt_strike(step),
        "premium_note": "Black-Scholes estimates - use live broker quotes before any trade.",
        "chain_estimate": rows, "global_cues": cues,
    }, f"brief_{m['name']}.json")


def cmd_orb(a, quiet=False) -> dict:
    m = meta(a.u)
    minutes = a.minutes or (load_config() or DEFAULTS).get("orb_minutes", 15)
    i1 = bars(m["yf"], "1d", "1m")
    today = i1[i1.index.date == i1.index[-1].date()]
    start = datetime.combine(today.index[-1].date(), OPEN, IST)
    orw = today[today.index < start + timedelta(minutes=minutes)]
    if orw.empty:
        res = {"underlying": m["name"], "status": "not open yet"}
        print(json.dumps(res)); return res
    hi, lo = float(orw["High"].max()), float(orw["Low"].min())
    after = today[today.index >= start + timedelta(minutes=minutes)]
    tp = (today["High"] + today["Low"] + today["Close"]) / 3
    vol = today["Volume"].replace(0, np.nan)
    vwap = float((tp * vol).sum() / vol.sum()) if vol.notna().any() else float(tp.mean())
    status, when, px = "inside range", None, None
    for ts, row in after.iterrows():
        if row["Close"] > hi:
            status, when, px = "broke UP", ts, float(row["Close"]); break
        if row["Close"] < lo:
            status, when, px = "broke DOWN", ts, float(row["Close"]); break
    res = {"underlying": m["name"], "as_of": today.index[-1].strftime("%H:%M"), "or_minutes": minutes,
           "or_high": round(hi, 2), "or_low": round(lo, 2), "or_mid": round((hi + lo) / 2, 2),
           "or_width": round(hi - lo, 2), "last": round(float(today["Close"].iloc[-1]), 2),
           "vwap": round(vwap, 2), "vwap_is_approx": not vol.notna().any(),
           "status": status, "break_time": when.strftime("%H:%M") if when is not None else None, "break_close": px,
           "note": "Yahoo data can lag a few minutes; confirm on your broker before acting."}
    save(f"orb_{m['name']}.json", res)
    if not quiet:
        print(json.dumps(res, indent=2, default=str))
    return res


def cmd_watch(a) -> None:
    import time as _t
    import winsound
    until = time.fromisoformat(a.until)
    print(f"Watching {a.u} opening range until {a.until} IST (Ctrl+C to stop)", flush=True)
    while now_ist().time() < until:
        try:
            r = cmd_orb(a, quiet=True)
            print(f"{now_ist():%H:%M:%S} {r.get('status')} last={r.get('last')} OR={r.get('or_low')}-{r.get('or_high')}", flush=True)
            if r.get("status", "").startswith("broke"):
                for _ in range(3):
                    winsound.Beep(1200, 400)
                print(json.dumps(r, indent=2, default=str)); return
        except Exception as e:
            print("fetch error:", e, flush=True)
        _t.sleep(60)
    print("Window ended with no breakout.")


def cmd_strikes(a) -> None:
    m = meta(a.u)
    spot = spot_of(m)
    exp, lot, chain = next_expiry(m)
    atm, step = atm_of(chain, spot), strike_step(chain, spot)
    rows = [{"strike": fmt_strike(atm + o * step), "CE": symbol_for(m, chain, atm + o * step, "CE"),
             "PE": symbol_for(m, chain, atm + o * step, "PE")} for o in range(-a.width, a.width + 1)]
    out({"underlying": m["name"], "spot": round(spot, 2), "atm": fmt_strike(atm), "step": fmt_strike(step),
         "expiry": exp.isoformat(), "lot": lot, "strikes": rows,
         "broker_hint": "Fetch these from the broker connector (e.g. Kite quotes) for live LTP, OI, volume."})


def cmd_plan(a) -> None:
    cfg = need_config()
    m = meta(a.u)
    spot = a.spot or spot_of(m)
    iv_est, iv_src = implied_vol(m)
    iv = a.iv / 100 if a.iv else iv_est
    exp, lot, chain = next_expiry(m)
    T = years_to(exp)
    step = strike_step(chain, spot)
    strike = a.strike or atm_of(chain, spot)
    kind, sign = a.side, (1 if a.side == "CE" else -1)
    g = bs(spot, strike, T, iv, kind)
    premium = a.premium or g["price"]
    stop_ix, tgt_ix = a.stop_level, a.target_level
    if stop_ix is None:
        try:
            stop_ix = json.loads((run_dir() / f"orb_{m['name']}.json").read_text(encoding="utf-8"))["or_mid"]
        except Exception:
            stop_ix = spot - sign * 0.5 * atr(bars(m["yf"], "1y", "1d"))
    if tgt_ix is None:
        tgt_ix = spot + sign * 2 * abs(spot - stop_ix)
    if not (sign * (spot - stop_ix) > 0 and sign * (tgt_ix - spot) > 0):
        out({"refused": "stop/target on the wrong side of the price for this option type",
             "spot": spot, "stop_level": stop_ix, "target_level": tgt_ix}); return
    later = max(T - 45 / (365 * 24 * 60), 1 / (365 * 24 * 4))
    scale = premium / g["price"] if g["price"] > 0 else 1

    def value(level: float, k: float) -> float:
        return bs(level, k, later, iv, kind)["price"]

    spread = None
    if a.spread_width:
        if cfg.get("style") != "buy_and_spreads":
            out({"refused": "Spreads are off in your settings (style=buy_only). Change with setup --style buy_and_spreads."}); return
        short_k = strike + sign * a.spread_width * step
        short_prem = a.short_premium or bs(spot, short_k, T, iv, kind)["price"]
        debit = premium - short_prem
        val_stop = max(value(stop_ix, strike) - value(stop_ix, short_k), 0) * scale
        val_tgt = max(value(tgt_ix, strike) - value(tgt_ix, short_k), 0) * scale
        spread = {"sell_strike": fmt_strike(short_k), "sell_contract": symbol_for(m, chain, short_k, kind),
                  "sell_premium": round(short_prem, 2), "net_debit": round(debit, 2),
                  "max_loss_per_lot": round(debit * lot), "max_profit_per_lot": round((abs(short_k - strike) - debit) * lot)}
        entry, stop_val, tgt_val = debit, round(val_stop, 1), round(val_tgt, 1)
    else:
        entry, stop_val, tgt_val = premium, round(value(stop_ix, strike) * scale, 1), round(value(tgt_ix, strike) * scale, 1)

    loss_per_lot = (entry - stop_val) * lot
    lots_risk = int(cfg["max_loss_per_trade_inr"] // loss_per_lot) if loss_per_lot > 0 else 0
    lots_cap = int(cfg["capital_inr"] * cfg["max_capital_per_trade_pct"] / 100 // max(entry * lot, 1))
    lots = max(0, min(lots_risk, lots_cap, int(cfg["max_lots"])))
    warn = []
    t = now_ist().time()
    if exp == now_ist().date() and not cfg.get("trade_expiry_day"):
        warn.append("EXPIRY DAY and your settings say no expiry-day buying.")
    if t > time.fromisoformat(cfg["no_new_entries_after"]):
        warn.append(f"After your no-new-entries time ({cfg['no_new_entries_after']}).")
    first = (datetime.combine(date.today(), OPEN) + timedelta(minutes=int(cfg["no_trade_first_minutes"]))).time()
    if OPEN <= t < first:
        warn.append(f"Inside your no-trade opening window (until {first:%H:%M}).")
    if lots == 0:
        warn.append("0 lots: one lot breaks your per-trade loss or capital limit. Skip, or pick a closer valid stop / cheaper strike.")
    if not m["is_index"]:
        warn.append("Stock option: check liquidity (bid-ask spread) and that it is not in the F&O ban list today.")
    out({
        "underlying": m["name"], "spot": round(spot, 2), "type": kind, "strike": fmt_strike(strike),
        "contract": symbol_for(m, chain, strike, kind), "expiry": exp.isoformat(), "lot_size": lot,
        "entry_premium": round(premium, 2), "premium_source": "given/broker" if a.premium else f"estimate ({iv_src}) - check broker!",
        "greeks_est": g, "spread": spread,
        "stop_level": round(stop_ix, 2), "target_level": round(tgt_ix, 2),
        "position_value_at_stop": stop_val, "position_value_at_target": tgt_val,
        "loss_per_lot_at_stop": round(loss_per_lot), "lots": lots, "quantity": lots * lot,
        "total_risk_inr": round(loss_per_lot * lots), "capital_used_inr": round(entry * lot * lots),
        "reward_risk": round((tgt_val - entry) / (entry - stop_val), 2) if entry > stop_val else None,
        "square_off_by": cfg["square_off_by"], "mode": cfg["mode"], "warnings": warn,
        "how_to_place": "Place it yourself in your broker app: BUY (and SELL leg for a spread), intraday product, LIMIT price, then a stop-loss order. The desk never places orders.",
    }, f"plan_{m['name']}_{kind}_{fmt_strike(strike)}.json")


def cmd_odds(a) -> None:
    m = meta(a.u)
    df = bars(m["yf"], "60d", "5m")
    df["ret"] = np.log(df["Close"]).diff()
    df["slot"] = df.index.strftime("%H:%M")
    df["day"] = df.index.date
    df = df[df["day"] == df["day"].shift()]
    end_slot = (load_config() or DEFAULTS)["square_off_by"]
    t0 = a.at or (now_ist().strftime("%H:%M") if OPEN <= now_ist().time() < CLOSE else "09:30")
    mm = int(t0[:2]) * 60 + int(t0[3:]); mm -= mm % 5
    slots = [f"{x // 60:02d}:{x % 60:02d}" for x in range(mm, int(end_slot[:2]) * 60 + int(end_slot[3:]), 5)]
    pools = [p for p in (df.loc[df["slot"] == s, "ret"].dropna().values for s in slots) if len(p) > 5]
    rng = np.random.default_rng(7)
    up = a.target > a.entry
    ht = hs = 0
    finals = []
    for _ in range(a.sims):
        px, res = a.entry, None
        for p in pools:
            px *= math.exp(rng.choice(p))
            if (px >= a.target) if up else (px <= a.target):
                res = "t"; break
            if (px <= a.stop) if up else (px >= a.stop):
                res = "s"; break
        ht += res == "t"; hs += res == "s"; finals.append(px)
    n = a.sims
    out({"underlying": m["name"], "from": t0, "to": end_slot, "entry": a.entry, "stop": a.stop, "target": a.target,
         "sims": n, "p_target_first": round(ht / n, 3), "p_stop_first": round(hs / n, 3),
         "p_neither_by_squareoff": round(1 - (ht + hs) / n, 3),
         "price_when_trade_ends_pctiles": {q: round(float(np.percentile(finals, q)), 1) for q in (10, 50, 90)},
         "breakeven_hit_rate_for_RR": round(abs(a.entry - a.stop) / (abs(a.entry - a.stop) + abs(a.target - a.entry)), 3),
         "note": "Underlying-level odds from the last 60 days; options also lose time value. Not a promise."},
        f"odds_{m['name']}.json")


JCOLS = ["id", "date", "time_in", "underlying", "contract", "side", "lots", "lot_size", "entry", "stop", "target",
         "time_out", "exit", "pnl_inr", "mode", "reason", "notes"]


def cmd_journal(a) -> None:
    p = ROOT / "journal.csv"
    rows = list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []
    today = now_ist().strftime("%Y-%m-%d")
    if a.action == "add":
        cfg = need_config()
        todays = [r for r in rows if r["date"] == today]
        lost = -sum(float(r["pnl_inr"]) for r in todays if r.get("pnl_inr") not in (None, "") and float(r["pnl_inr"]) < 0)
        if len(todays) >= int(cfg["max_trades_per_day"]):
            print(json.dumps({"blocked": f"Daily trade limit reached ({cfg['max_trades_per_day']})."})); return
        if lost >= float(cfg["max_loss_per_day_inr"]):
            print(json.dumps({"blocked": f"Daily loss limit hit (lost Rs {lost:.0f})."})); return
        rows.append({"id": str(len(rows) + 1), "date": today, "time_in": now_ist().strftime("%H:%M"),
                     "underlying": a.u, "contract": a.contract, "side": a.side, "lots": a.lots, "lot_size": a.lot_size,
                     "entry": a.entry, "stop": a.stop, "target": a.target, "mode": a.mode or cfg["mode"],
                     "reason": a.reason or "", "notes": ""})
        print(f"logged trade #{len(rows)} ({a.mode or cfg['mode']})")
    elif a.action == "close":
        r = next(r for r in rows if r["id"] == str(a.id))
        r["exit"], r["time_out"] = a.exit, now_ist().strftime("%H:%M")
        r["pnl_inr"] = round((float(a.exit) - float(r["entry"])) * int(r["lots"]) * int(r["lot_size"]))
        r["notes"] = a.notes or ""
        print(f"closed #{a.id}: P&L Rs {r['pnl_inr']} (before charges)")
    elif a.action == "list":
        for r in rows[-20:]:
            print(r)
        return
    elif a.action == "stats":
        pn = [float(r["pnl_inr"]) for r in rows if r.get("pnl_inr") not in (None, "")]
        wins = [x for x in pn if x > 0]
        losses = [x for x in pn if x <= 0]
        todays = [float(r["pnl_inr"]) for r in rows if r["date"] == today and r.get("pnl_inr") not in (None, "")]
        out({"closed_trades": len(pn), "win_rate": round(len(wins) / len(pn), 3) if pn else None,
             "total_pnl_before_charges": round(sum(pn)), "today_pnl": round(sum(todays)),
             "avg_win": round(np.mean(wins)) if wins else None, "avg_loss": round(np.mean(losses)) if losses else None,
             "charges_note": "Allow ~Rs 50-70 per round-trip option order (brokerage, STT, exchange fees, GST)."})
        return
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=JCOLS)
        w.writeheader()
        w.writerows({k: r.get(k, r.get("index", "")) if k == "underlying" else r.get(k, "") for k in JCOLS} for r in rows)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, underlying=True):
        p = sp.add_parser(name)
        if underlying:
            p.add_argument("--u", "--underlying", "--index", dest="u", type=str.upper, default=None)
        p.set_defaults(fn=fn)
        return p

    p = add("setup", cmd_setup, False)
    for k, (typ, h) in SETTINGS.items():
        p.add_argument("--" + k.replace("_", "-").replace("-inr", ""), dest=k, type=str, help=h)
    for k, v in DEFAULTS.items():
        p.add_argument("--" + k.replace("_", "-"), dest=k, help=f"default {v}",
                       type=(lambda s: s.lower() in ("1", "true", "yes")) if isinstance(v, bool) else type(v))
    add("settings", cmd_settings, False)
    add("doctor", cmd_doctor, False)
    add("brief", cmd_brief)
    p = add("orb", cmd_orb); p.add_argument("--minutes", type=int)
    p = add("watch", cmd_watch); p.add_argument("--minutes", type=int); p.add_argument("--until", default="11:00")
    p = add("strikes", cmd_strikes); p.add_argument("--width", type=int, default=5)
    p = add("plan", cmd_plan)
    p.add_argument("--side", required=True, type=str.upper, choices=["CE", "PE"])
    for k in ("strike", "premium", "spot", "iv", "stop-level", "target-level", "short-premium"):
        p.add_argument(f"--{k}", type=float)
    p.add_argument("--spread-width", type=int, help="debit spread: sell N strikes further out")
    p = add("odds", cmd_odds)
    for k in ("entry", "stop", "target"):
        p.add_argument(f"--{k}", type=float, required=True)
    p.add_argument("--sims", type=int, default=10000); p.add_argument("--at")
    p = add("journal", cmd_journal)
    p.add_argument("action", choices=["add", "close", "list", "stats"])
    for k in ("contract", "side", "reason", "notes", "mode"):
        p.add_argument(f"--{k}")
    for k in ("lots", "lot-size", "id"):
        p.add_argument(f"--{k}", type=int)
    for k in ("entry", "stop", "target", "exit"):
        p.add_argument(f"--{k}", type=float)
    a = ap.parse_args()
    if hasattr(a, "u") and a.u is None:
        a.u = default_underlying()
    if hasattr(a, "u") and not (a.cmd == "journal" and a.action != "add"):
        instruments(meta(a.u))  # friendly error if the symbol has no options
    a.fn(a)


if __name__ == "__main__":
    main()
