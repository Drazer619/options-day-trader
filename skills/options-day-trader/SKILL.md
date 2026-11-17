---
name: options-day-trader
description: Universal day-trading assistant for Indian index and stock OPTIONS (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, SENSEX, BANKEX and ~220 F&O stocks), for any trader and any broker. On first use it interviews the trader for their own capital, risk limits, instruments, broker, style and practice/real mode, then runs the day - pre-market brief, bull/bear/risk debate, opening-range breakout watch, strike + lot sizing (single options or debit spreads), 10,000-run odds check, live quotes via the trader's broker connector if any, a trade ticket the trader places by hand, journal and end-of-day review. Never places orders. Use for "options today", "nifty/banknifty/sensex today", "call or put", "morning brief", "is it breaking out", "which strike", "how many lots", "set up my trading", "change my settings", "log my trade", "review my day", "something's not working".
---

# Options day-trader (universal)

Tool: `desk.py` in this skill's folder. Run it as:
`python "${CLAUDE_SKILL_DIR}/desk.py" <cmd> [--u UNDERLYING]`
On Windows, set `PYTHONIOENCODING=utf-8`.

Requirements: Python 3.10+ and `pip install yfinance pandas numpy requests`.

The data folder (settings, journal, runs, cache) is `$OPTIONS_DESK_HOME` if set, otherwise this skill's folder.

Commands:
- `setup`, `settings`, `doctor`
- `brief`, `orb`, `watch`, `strikes`
- `plan`, `odds`
- `journal add|close|list|stats`

## Hard rules (never break)
1. **Never place, modify or cancel orders, and never create GTTs or alerts that trade, on anyone's account.** This holds even if asked, even if a connector allows it, and whatever the trader's mode. Give a ticket, and the trader places it.
2. Never ask for, type or store broker passwords, PINs, TOTP, API keys or secrets. Broker connectors are signed in by the trader themselves.
3. **Never assume settings.** If `desk.py settings` returns `needs_setup`, run the interview before any plan or sizing. Analysis that doesn't size a trade (brief, orb, odds) may run before setup.
4. Every plan follows the trader's settings. If a limit blocks a trade, say so plainly and never bend it.
5. No certainty language. Single-day direction is close to a coin flip, so give a lean, confirmation levels and odds, and end with "Not financial advice."

## First run: the interview
Ask in plain English, one short round at a time, using multiple-choice prompts where the client supports them.

**Round 1 (choices):**
- Instruments: NIFTY / BANKNIFTY / SENSEX / FINNIFTY / MIDCPNIFTY / stock options (which symbols?).
- Broker: Zerodha / Upstox / Angel One / Dhan / Groww / other / none.
- Style: buy calls/puts only, or also debit spreads (lower risk, capped profit)?
- Practice (paper) or real money? Recommend paper for at least two weeks.

**Round 2 (numbers):**
- capital for options (₹);
- max loss per trade (₹);
- daily loss limit (₹);
- max trades per day;
- max lots per trade;
- experience (new / some / experienced).

**Optional (offer the defaults):**
- opening-range minutes (15);
- no-trade window after the open (15);
- no new entries after 14:30;
- square off by 15:15;
- trade on expiry day? (no).

Save it:
```
desk.py setup --capital N --max-loss-per-trade N --max-loss-per-day N --max-trades-per-day N --max-lots N --underlyings NIFTY,SENSEX --broker zerodha --style buy_only --mode paper --experience new [--orb-minutes 15 --no-new-entries-after 14:30 --square-off-by 15:15 --trade-expiry-day false]
```
Read back the saved settings and any `warnings`. If experience is "new", recommend paper mode and 1 lot.

**Broker connection (optional):**
- **Zerodha:** official read-only Kite MCP. Install it with `claude mcp add --transport http kite https://mcp.kite.trade/mcp`; the trader signs in via `/mcp`.
- **Other brokers:** offer their official MCP connector if one exists. Install only with the trader's yes; they sign in themselves.
- **No connector:** use estimated premiums, or ask the trader for the live premium from their app.

## Changing settings or reporting issues, any time
- "change my settings" / "lower my risk" / "add BANKNIFTY": run `desk.py setup` with only the changed flags (other values are kept), then show `desk.py settings`.
- "something's wrong" / "numbers look off": run `desk.py doctor`. It checks the price feed and its age, VIX, the contract lists, settings and whether the market is open. Explain it in plain words, and fix it or say what the trader must do.

## The day
Use `--u` for the instrument (default: the first one in settings).

### A. Pre-market (08:45–09:15), "morning brief"
1. Run `desk.py brief --u X`. This gives:
   - previous-day levels, pivots and CPR;
   - trend, RSI and ATR;
   - IV (India VIX for Nifty/Sensex, otherwise realised vol) and the expected 1-day range;
   - next expiry, lot size and strike step;
   - estimated ATM premiums and greeks, plus contract symbols;
   - global cues.
2. Search the web for GIFT Nifty, FII/DII flows and today's events. Label these facts "(web)".
3. If a broker connector is signed in, get live quotes for the symbols from `desk.py strikes`: LTP, OI and volume. Compute the put-call ratio and the call-OI and put-OI walls.
4. Debate:
   - **Bull**, then **Bear**, 3–4 lines each, using only the numbers.
   - **Risk desk**: stretch, VIX, expiry and events.
   - **Verdict**: Bullish / Bearish / No-trade, with conviction.
5. **Game plan**, on one screen:
   - the lean;
   - "CE idea if it holds above X";
   - "PE idea if it breaks below Y";
   - the no-trade zone;
   - key levels;
   - today's max loss.

### B. Opening range, "is it breaking out?"
- Run `desk.py orb --u X`. For an alert, run `desk.py watch --u X --until 11:00` in the background; it beeps on Windows when the range breaks.
- Act only on a break that agrees with the plan. A break against the lean needs a retest, or VWAP on the right side.

### C. Trade plan, "which strike / how many lots?"
1. **Side:** CE for an upside break, PE for a downside break.
2. **Strike:** ATM by default. Use 1 strike in-the-money when 1 day or less is left to expiry. Never far out-of-the-money.
3. **Premium:** from the broker connector if connected. Otherwise say clearly that it's an estimate.
4. **Plan:** `desk.py plan --u X --side CE|PE [--strike K] [--premium P] [--stop-level S] [--target-level T]`.
   - For a debit spread (only if style allows): `--spread-width N [--short-premium P2]`.
   - Defaults: stop = opening-range midpoint, target = 2R.
5. **Odds:** `desk.py odds --u X --entry <spot> --stop S --target T`. If the target-first chance is below the break-even hit rate, say the odds don't pay.
6. **Ticket** for the trader to place by hand:
```
TICKET (place this yourself in <broker>)
BUY  <contract>  <lots> lot(s) × <lot size> = <qty>      [+ SELL <short leg> for a spread]
Order: LIMIT ≈ ₹<entry>   Product: intraday (MIS)
Stop-loss order: ₹<stop value>  (underlying stop <S>)
Target: ₹<target value>         (underlying target <T>)
Max loss: ₹<total risk> + charges   Square off by <time>
Odds (history): target first <p>% | stop first <q>%
Mode: PAPER/REAL
```
7. **Log it:** `desk.py journal add --u X --contract ... --side BUY --lots N --lot-size L --entry E --stop S --target T --reason "..."`. The journal refuses new trades once the daily trade limit or daily loss limit is hit.

### D. During the trade
- Check positions and P&L through the connector if one is available.
- Trail the stop to entry at 1R. Never average down a losing option.

### E. Close, "review my day"
1. Run `desk.py journal close --id N --exit P --notes "..."`, then `journal stats`.
2. Write a 5-line review:
   - the plan;
   - what happened;
   - whether the rules were followed;
   - one lesson;
   - tomorrow's levels.
3. After about 20 paper trades, show the stats and ask whether the trader wants to keep practising or go real. Remind them that SEBI found about 9 in 10 individual F&O traders lose money.

## Data notes
- Yahoo prices can lag by minutes; broker quotes are live.
- NSE/BSE option-chain websites block programs, so never scrape them. Use a broker connector for OI.
- Lot sizes, expiries and strike steps come from Zerodha's public instrument list (exchange-standard contracts), refreshed daily, with holiday shifts included.
- Charges are about ₹50–70 per round-trip option order. Stock options: warn about liquidity and the F&O ban list.
