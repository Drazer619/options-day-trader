# Options Day-Trader

A Claude Code skill and Python tool for **intraday options on Indian markets**: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, SENSEX, BANKEX and about 220 F&O stocks. It works with any broker.

It's like having a trading desk in your chat:
- a pre-market brief;
- a bull vs bear debate;
- an opening-range breakout watch;
- strike and lot sizing to *your* risk limits;
- 10,000-run odds simulations;
- a trade ticket;
- a journal and an end-of-day review.

> **It never places orders.** It gives you a ticket, and you place it yourself in your broker app. It's analysis and practice software, **not financial advice**. SEBI found about 9 in 10 individual F&O traders lose money. Start in paper mode.

## What it does

| Say to Claude | What happens |
|---|---|
| "set up my trading" | First-run interview: instruments, broker, style, paper or real, capital, loss limits, lots. Nothing is assumed. |
| "morning brief" | Previous-day levels, pivots, trend, VIX-implied range, next expiry, lot size, estimated premiums and greeks, global cues and news, then a bull/bear/risk verdict and a game plan |
| "is it breaking out?" | Opening-range status and VWAP. It can also watch in the background and beep on a break. |
| "which strike / how many lots?" | Strike, premium stop and target, lots sized to your max loss, plus debit spreads if you enabled them |
| "what are the odds?" | Replays the rest of the day 10,000 times from the last 60 days of 5-minute bars |
| "log my trade" / "review my day" | Journal with daily trade and loss limits enforced, stats and a lesson |
| "change my settings" / "something's not working" | Updates one setting, or runs a health check (`doctor`) |

## Install

**1. Python packages** (Python 3.10+):
```bash
pip install -r requirements.txt
```

**2. The skill.** Pick one:
- **As a plugin**, inside Claude Code:
  ```
  /plugin marketplace add Drazer619/options-day-trader
  /plugin install options-day-trader@options-day-trader
  ```
- **Or copy it:** put `skills/options-day-trader/` into `~/.claude/skills/` (global) or into your project's `.claude/skills/`.

**3. Optional: where it saves your data.** By default it uses the skill folder. To keep settings, journal and runs somewhere else:
```bash
export OPTIONS_DESK_HOME=~/.options-desk
```

**4. Optional: live prices for Zerodha users.** This uses Zerodha's official, free, read-only Kite MCP:
```bash
claude mcp add --transport http kite https://mcp.kite.trade/mcp
```
Then sign in from `/mcp`. Without a connector, premiums are Black-Scholes estimates and the agent asks you for the live price.

## Use the tool without Claude

```bash
cd skills/options-day-trader
python desk.py doctor
python desk.py brief --u NIFTY
python desk.py orb --u BANKNIFTY
python desk.py setup --capital 100000 --max-loss-per-trade 2000 --max-loss-per-day 4000 \
  --max-trades-per-day 3 --max-lots 2 --underlyings NIFTY,SENSEX --broker zerodha \
  --style buy_only --mode paper --experience new
python desk.py plan --u NIFTY --side CE
python desk.py odds --u NIFTY --entry 25400 --stop 25330 --target 25540
python desk.py journal stats
```
Every command prints JSON and saves a copy under `runs/<date>/`.

## Data sources (all free)
- **Prices and indicators:** Yahoo Finance via `yfinance`. These can lag by a few minutes.
- **Contracts, lot sizes, expiries, strike steps:** Zerodha's public instrument list (`api.kite.trade/instruments`), which needs no login. Contracts are exchange-standard, so this works for every broker.
- **Live option prices and open interest:** your broker's connector, if you add one. NSE/BSE websites block programs, and this tool does not scrape them.

## Limits and honest caveats
- Premiums are **estimates** unless you supply live ones.
- Index IV uses India VIX. Other underlyings use 20-day realised volatility.
- Holidays aren't checked in `doctor`, but expiries already include holiday shifts.
- Odds come from history and are not a forecast. Options also lose time value.
- Long options and debit spreads only. No option selling or naked short positions.

## Tests

```bash
python -m unittest discover -s tests -v
```
The tests run offline (no network): pricing maths, strike logic, setup validation and journal limits.

## Contributing
Issues and pull requests are welcome. Please test in paper mode, and never add code that places orders.

## License
MIT. See [LICENSE](LICENSE).
