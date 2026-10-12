"""
Pretend Dogecoin trading A/B test. NO real money and NO exchange account.

Every run it fetches the live DOGE price, lets four strategies each make a
pretend trade decision with their own $100, saves everything to
doge_state.json and doge_history.csv, and once a day (after 8 PM Eastern)
sends a leaderboard to your phone through ntfy.
"""

import csv
import json
import os
import random
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

START_CASH = 100.00
FEE = 0.005                 # pretend 0.5% fee on every trade, like a real exchange
SUMMARY_HOUR_ET = 20        # send the daily leaderboard after 8 PM Eastern
STATE_FILE = "doge_state.json"
HISTORY_FILE = "doge_history.csv"
ET = ZoneInfo("America/New_York")

STRATEGIES = {
    "hold":   "Buy & hold",
    "dip":    "Buy dips, sell rises",
    "ma":     "Moving-average crossover",
    "coin":   "Coin flip",
}

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
TEST_SUMMARY = os.environ.get("TEST_SUMMARY", "").lower() == "true"


# ---------- prices ----------

def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "doge-paper-bot"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def get_price():
    if os.environ.get("FAKE_PRICE"):
        return float(os.environ["FAKE_PRICE"])
    sources = [
        ("Coinbase", lambda: float(_get_json(
            "https://api.coinbase.com/v2/prices/DOGE-USD/spot")["data"]["amount"])),
        ("Kraken", lambda: float(list(_get_json(
            "https://api.kraken.com/0/public/Ticker?pair=XDGUSD")["result"].values())[0]["c"][0])),
        ("CoinGecko", lambda: float(_get_json(
            "https://api.coingecko.com/api/v3/simple/price?ids=dogecoin&vs_currencies=usd")["dogecoin"]["usd"])),
    ]
    for name, fetch in sources:
        try:
            price = fetch()
            print(f"DOGE price from {name}: ${price:.5f}")
            return price
        except Exception as err:
            print(f"{name} price failed: {err}")
    return None


# ---------- pretend trading ----------

def new_wallet():
    return {"cash": START_CASH, "doge": 0.0, "ref": None, "signal": None, "trades": 0}


def buy(w, dollars, price, name, log, now):
    dollars = min(dollars, w["cash"])
    if dollars < 1:
        return
    w["cash"] -= dollars
    w["doge"] += dollars * (1 - FEE) / price
    w["trades"] += 1
    w["ref"] = price
    log.append(f"{now} {name}: BUY ${dollars:.2f} at ${price:.5f}")


def sell(w, doge_amount, price, name, log, now):
    doge_amount = min(doge_amount, w["doge"])
    if doge_amount * price < 1:
        return
    w["doge"] -= doge_amount
    w["cash"] += doge_amount * price * (1 - FEE)
    w["trades"] += 1
    w["ref"] = price
    log.append(f"{now} {name}: SELL {doge_amount:.1f} DOGE at ${price:.5f}")


def average(values):
    return sum(values) / len(values)


def run_strategies(state, price, now):
    wallets, prices, log = state["wallets"], state["prices"], state["log"]

    # 1) Buy & hold: go all in on the first run, then never touch it.
    w = wallets["hold"]
    if w["trades"] == 0:
        buy(w, w["cash"], price, "hold", log, now)

    # 2) Buy dips, sell rises: buy $25 after a 5% drop from the last trade price,
    #    sell a quarter of the DOGE after a 5% rise. While holding no DOGE,
    #    the reference price follows the price up so it can still catch dips.
    w = wallets["dip"]
    if w["ref"] is None:
        w["ref"] = price
    elif w["doge"] == 0 and price > w["ref"]:
        w["ref"] = price
    if price <= w["ref"] * 0.95 and w["cash"] >= 1:
        buy(w, 25, price, "dip", log, now)
    elif price >= w["ref"] * 1.05 and w["doge"] > 0:
        sell(w, w["doge"] * 0.25, price, "dip", log, now)

    # 3) Moving-average crossover: compare the 4-hour average (16 checks) with the
    #    24-hour average (96 checks). Go all in when short crosses above long,
    #    sell everything when it crosses below. Waits ~1 day to collect prices.
    w = wallets["ma"]
    if len(prices) >= 96:
        short, long_ = average(prices[-16:]), average(prices[-96:])
        signal = "up" if short > long_ else "down"
        if signal != w["signal"]:
            if signal == "up":
                buy(w, w["cash"], price, "ma", log, now)
            elif w["signal"] is not None:
                sell(w, w["doge"], price, "ma", log, now)
            w["signal"] = signal

    # 4) Coin flip: once an hour, randomly buy $5 or sell $5 worth.
    w = wallets["coin"]
    if datetime.now(ET).minute < 15 or w["trades"] == 0:
        if random.random() < 0.5:
            buy(w, 5, price, "coin", log, now)
        else:
            sell(w, 5 / price, price, "coin", log, now)

    state["log"] = log[-300:]


def value(w, price):
    return w["cash"] + w["doge"] * price


# ---------- notifications ----------

def notify(title, message):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC secret is missing; summary not sent.\n" + message)
        return
    req = urllib.request.Request(
        "https://ntfy.sh/" + NTFY_TOPIC, data=message.encode("utf-8"),
        headers={"Title": title, "Tags": "dog,chart_with_upwards_trend", "Priority": "default"},
    )
    urllib.request.urlopen(req, timeout=20)
    print("Summary sent.")


def leaderboard(state, price):
    rows = sorted(state["wallets"].items(), key=lambda kv: value(kv[1], price), reverse=True)
    lines = [f"DOGE ${price:.4f}  (pretend money, 0.5% fees)"]
    medals = ["🥇", "🥈", "🥉", "4."]
    for i, (key, w) in enumerate(rows):
        v = value(w, price)
        change = (v - START_CASH) / START_CASH * 100
        lines.append(f"{medals[i]} {STRATEGIES[key]}: ${v:.2f} ({change:+.1f}%), {w['trades']} trades")
    started = state.get("started", "")[:10]
    lines.append(f"Running since {started}")
    return "\n".join(lines)


# ---------- main ----------

def main():
    price = get_price()
    if price is None:
        print("No price source answered; skipping this run.")
        return

    now_et = datetime.now(ET)
    stamp = now_et.strftime("%Y-%m-%d %H:%M")

    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
    else:
        state = {"started": stamp, "wallets": {k: new_wallet() for k in STRATEGIES},
                 "prices": [], "log": [], "last_summary": ""}
        print("Starting a fresh A/B test with $100 per strategy.")

    state["prices"] = (state["prices"] + [price])[-200:]
    run_strategies(state, price, stamp)

    new_file = not os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["time_et", "doge_price"] + [f"{k}_value" for k in STRATEGIES])
        writer.writerow([stamp, f"{price:.5f}"] +
                        [f"{value(state['wallets'][k], price):.2f}" for k in STRATEGIES])

    today = now_et.strftime("%Y-%m-%d")
    board = leaderboard(state, price)
    print(board)
    if TEST_SUMMARY:
        notify("Doge bot leaderboard (test)", board)
    elif now_et.hour >= SUMMARY_HOUR_ET and state.get("last_summary") != today:
        notify("Doge bot daily leaderboard", board)
        state["last_summary"] = today

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)


if __name__ == "__main__":
    main()
