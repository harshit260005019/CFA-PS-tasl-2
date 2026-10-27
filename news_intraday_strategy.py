#!/usr/bin/env python3
"""
News-driven intraday long/short strategy on Indian large-cap equities (CFA Club induction, PS2)

What this script does
---------------------
1. Loads daily OHLCV data (real NSE data through yfinance, or a synthetic market for testing).
2. Cleans it and checks it.
3. Flags "news days" using only OHLCV: an unusually big close-to-close move on unusually high volume.
4. The NEXT session it trades in the direction of that move, intraday only:
      good-news day -> BUY at the open, square off at the close
      bad-news day  -> SHORT at the open, buy back at the close
   The signal is built from today's close and executed at tomorrow's open, so nothing from the
   future leaks in.
5. Backtests with NSE intraday charges, slippage, a stop-loss and position sizing.
6. Tunes the two news thresholds on a development period only, then runs the later test period once.
7. Writes metrics, a trade log, charts and a results.json that build_report.py turns into the PDF.

How to run
----------
    pip install numpy pandas matplotlib            # always needed
    pip install yfinance                           # only for real data

    python news_intraday_strategy.py --source synthetic --out outputs     # works offline
    python news_intraday_strategy.py --source yfinance  --out outputs     # real NSE data, needs internet
    python build_report.py --results outputs/results.json --out report.pdf

Python 3.10+.  Everything that is an assumption lives in CFG below so it can be quoted in the report.
"""
import argparse
import json
import math
import os
from copy import deepcopy

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------------------------
# SETTINGS (every assumption in one place)
# ----------------------------------------------------------------------------------------------
CFG = dict(
    tickers=["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "ITC", "LT"],
    data_start="2015-01-01",     # earlier than dev_start so rolling windows are warm on day one
    data_end="2026-09-30",
    dev_start="2016-01-01", dev_end="2021-12-31",      # development period: tune here
    test_start="2022-01-01", test_end="2026-09-30",    # test period: looked at once, at the end
    capital=1_000_000,           # Rs 10 lakh at the start of each period
    ret_window=20,               # days used to measure a "normal" daily move
    vol_window=10,               # days for average volume (Varsity, Technical Analysis ch. 12 uses 10)
    atr_window=14,               # days for average true range (stop distance)
    stop_atr=1.5,                # stop-loss = 1.5 x ATR away from entry
    risk_per_trade=0.005,        # lose at most 0.5% of equity if the stop is hit
    max_pos_frac=0.20,           # no single position larger than 20% of equity
    max_positions=4,             # at most 4 trades per day (biggest news first)
    slippage=0.0005,             # 0.05% worse than the quoted price on every fill
    rf=0.0,                      # risk-free rate for the headline Sharpe/Sortino (see report: capital is free every evening)
    rf_alt=0.06,                 # a 6% a year rate, shown next to it for comparison
    grid_k=[2.0, 2.5, 3.0],      # news threshold: move must be >= k normal daily moves
    grid_m=[1.5, 2.0, 3.0],      # news threshold: volume must be >= m x its 10-day average
    min_dev_trades=30,           # ignore settings that trade too rarely to judge
    split_quarantine=10,         # real data only: skip signals for 10 days after a stock split
    seed=20261019,
)
TRADING_DAYS = 252


# ----------------------------------------------------------------------------------------------
# NSE INTRADAY CHARGES (equity intraday column of zerodha.com/charges, read 19 Oct 2026)
# ----------------------------------------------------------------------------------------------
def leg_cost(value, side):
    """Charges on ONE order (one leg of a round trip). value = quantity x price."""
    brokerage = min(0.0003 * value, 20.0)       # 0.03% or Rs 20 per executed order, whichever is lower
    txn = 0.0000307 * value                      # NSE transaction charge
    sebi = value * 10 / 1e7                      # Rs 10 per crore
    gst = 0.18 * (brokerage + txn + sebi)        # 18% GST on brokerage + SEBI + transaction charges
    stamp = 0.00003 * value if side == "buy" else 0.0     # 0.003%, buy side only
    stt = 0.00025 * value if side == "sell" else 0.0      # 0.025%, sell side only
    return brokerage + txn + sebi + gst + stamp + stt


# ----------------------------------------------------------------------------------------------
# STEP 1: DATA
# ----------------------------------------------------------------------------------------------
def make_synthetic(cfg, drift_frac=0.10):
    """
    A made-up market so the pipeline can be tested offline. It has:
      * a market factor with bull / bear / sideways regimes and changing volatility
      * 8 stocks with their own noise, a gap at the open and an intraday move
      * random 'news days' (about 3 per stock per year) with a big move and a volume surge
      * a planted continuation: the day AFTER news, the intraday move leans in the news direction by
        drift_frac x the size of the news move.  drift_frac=0 is the 'nothing to find' world.
    Numbers from this market say nothing about real stocks.
    """
    rng = np.random.default_rng(cfg["seed"])
    dates = pd.bdate_range(cfg["data_start"], cfg["data_end"])     # Mon-Fri, no exchange holidays
    n, ns = len(dates), len(cfg["tickers"])

    drift = np.array([0.0012, -0.0012, 0.0001])        # bull, bear, sideways (per day)
    vol = np.array([0.8, 1.5, 0.9]) * 0.0085
    mean_dur = np.array([140, 55, 110])
    nxt = {0: ([2, 1], [0.7, 0.3]), 1: ([2, 0], [0.6, 0.4]), 2: ([0, 1], [0.55, 0.45])}
    state, s = np.empty(n, dtype=int), 2
    for t in range(n):
        state[t] = s
        if rng.random() < 1 / mean_dur[s]:
            s = rng.choice(nxt[s][0], p=nxt[s][1])

    def ar1(size, phi, sd):
        h = np.zeros(size)
        e = rng.normal(0, sd, size)
        for t in range(1, size):
            h[t] = phi * h[t - 1] + e[t]
        return h

    sig_m = vol[state] * np.exp(ar1(n, 0.97, 0.07))
    g_m = rng.normal(0.3 * drift[state], 0.5 * sig_m)             # market gap
    i_m = rng.normal(0.7 * drift[state], 0.85 * sig_m)            # market intraday move
    bench = pd.Series(10000 * np.exp(np.cumsum(g_m + i_m)), index=dates, name="NIFTY (synthetic)")

    panel = {}
    steps = 20
    k = np.arange(steps + 1) / steps
    for name in cfg["tickers"]:
        beta = rng.uniform(0.8, 1.3)
        sig0 = rng.uniform(0.009, 0.014)
        sig = sig0 * np.exp(ar1(n, 0.96, 0.08))
        news = rng.random(n) < 0.012
        sign = rng.choice([-1.0, 1.0], n)
        size = sig0 * rng.uniform(3, 7, n)
        overnight = rng.random(n) < 0.5
        shock = np.where(news, sign * size, 0.0)

        gap = beta * g_m + rng.normal(0, 0.5 * sig) + np.where(overnight, shock, 0.0)
        intra = beta * i_m + rng.normal(0, 0.85 * sig) + np.where(~overnight, shock, 0.0)
        intra[1:] += drift_frac * shock[:-1]                      # planted continuation the day after news
        logc = np.log(rng.uniform(200, 2500)) + np.cumsum(gap + intra)
        logo = logc - intra
        # Brownian-bridge path inside each day gives realistic highs and lows
        w = np.cumsum(rng.normal(0, 1 / math.sqrt(steps), (n, steps)), axis=1)
        w = np.hstack([np.zeros((n, 1)), w])
        bridge = w - k[None, :] * w[:, -1:]
        path = logo[:, None] + k[None, :] * intra[:, None] + (1.0 * sig)[:, None] * bridge
        o, c = np.exp(logo), np.exp(logc)
        h = np.maximum.reduce([np.exp(path.max(axis=1)), o, c])    # guard against float rounding
        l = np.minimum.reduce([np.exp(path.min(axis=1)), o, c])
        total = np.abs(gap + intra) / sig0
        v_noise = np.zeros(n)
        e = rng.normal(0, 0.2, n)
        for t in range(1, n):
            v_noise[t] = 0.8 * v_noise[t - 1] + e[t]
        mult = np.ones(n)
        mult[news] = rng.uniform(2, 4.5, news.sum())
        mult[1:] = np.where(news[:-1], np.maximum(mult[1:], 1.3), mult[1:])
        volume = rng.uniform(3e6, 12e6) * np.exp(v_noise) * (1 + 0.7 * total) * mult
        panel[name] = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c,
                                    "Volume": volume.round()}, index=dates)
    return panel, bench, {name: [] for name in cfg["tickers"]}


def load_yfinance(cfg):
    """Real NSE data. Prices are adjusted for splits and dividends (auto_adjust=True)."""
    import yfinance as yf                                     # imported here so offline runs do not need it
    end = (pd.Timestamp(cfg["data_end"]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")   # end is exclusive

    def grab(symbol):
        d = yf.download(symbol, start=cfg["data_start"], end=end, auto_adjust=True, progress=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        d.index = pd.to_datetime(d.index)
        if d.index.tz is not None:                            # make dates timezone-naive so they line up
            d.index = d.index.tz_localize(None)
        d.index = d.index.normalize()
        return d[["Open", "High", "Low", "Close", "Volume"]]

    panel, splits = {}, {}
    for name in cfg["tickers"]:
        panel[name] = grab(f"{name}.NS")
        sp = yf.Ticker(f"{name}.NS").splits
        splits[name] = [pd.Timestamp(x).tz_localize(None) for x in sp.index]
    bench = grab("^NSEI")["Close"].rename("NIFTY 50")
    return panel, bench, splits


# ----------------------------------------------------------------------------------------------
# STEP 2: CLEAN AND CHECK
# ----------------------------------------------------------------------------------------------
def clean(panel, bench):
    """Mark impossible rows as NaN (never forward-fill prices) and report what was found."""
    idx = bench.index
    report = {"benchmark_rows": int(len(bench)),
              "first_day": str(idx[0].date()), "last_day": str(idx[-1].date()), "stocks": {}}
    cleaned = {}
    for name, d in panel.items():
        d = d[~d.index.duplicated(keep="first")].reindex(idx)
        info = {"rows": int(d["Close"].notna().sum()), "missing_days": int(d["Close"].isna().sum())}
        bad_range = ((d["High"] < d[["Open", "Close"]].max(axis=1)) | (d["Low"] > d[["Open", "Close"]].min(axis=1)))
        bad_px = (d[["Open", "High", "Low", "Close"]] <= 0).any(axis=1)
        zero_vol = d["Volume"] <= 0
        info.update(bad_high_low=int(bad_range.sum()), non_positive_price=int(bad_px.sum()),
                    zero_volume=int(zero_vol.sum()))
        d = d.mask(bad_range | bad_px | zero_vol)
        jumps = d["Close"].pct_change().abs() > 0.20
        info["moves_over_20pct"] = int(jumps.sum())            # worth a look: unadjusted corporate action?
        cleaned[name] = d
        report["stocks"][name] = info
    return cleaned, report


# ----------------------------------------------------------------------------------------------
# STEP 3: INDICATORS (each uses only information available at that day's close)
# ----------------------------------------------------------------------------------------------
def add_indicators(d, cfg):
    d = d.copy()
    d["ret"] = d["Close"].pct_change()
    # 'normal' size of a daily move = std of the PREVIOUS 20 daily returns (today excluded)
    d["sd"] = d["ret"].rolling(cfg["ret_window"], min_periods=cfg["ret_window"]).std().shift(1)
    d["z"] = d["ret"] / d["sd"]
    # volume relative to the PREVIOUS 10-day average (today excluded)
    d["vavg"] = d["Volume"].rolling(cfg["vol_window"], min_periods=cfg["vol_window"]).mean().shift(1)
    d["vr"] = d["Volume"] / d["vavg"]
    pc = d["Close"].shift(1)
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - pc).abs(), (d["Low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(cfg["atr_window"], min_periods=cfg["atr_window"]).mean()
    return d


# ----------------------------------------------------------------------------------------------
# STEP 4: THE BACKTEST ENGINE
# ----------------------------------------------------------------------------------------------
class Market:
    """Holds the numbers as plain arrays so the day-by-day loop stays readable and fast."""

    def __init__(self, ind, bench, splits, cfg):
        self.names = list(ind)
        self.idx = bench.index
        self.bench = bench
        g = lambda col: np.vstack([ind[n][col].to_numpy(float) for n in self.names])
        self.O, self.H, self.L, self.C = g("Open"), g("High"), g("Low"), g("Close")
        self.Z, self.VR, self.ATR = g("z"), g("vr"), g("atr")
        self.quar = np.zeros_like(self.O, dtype=bool)
        for i, n in enumerate(self.names):
            for sd in splits.get(n, []):
                p = self.idx.searchsorted(sd)
                self.quar[i, p: p + cfg["split_quarantine"] + 1] = True


def run_backtest(mk, cfg, k, m, start, end, slippage=None, charges=True):
    """
    Walk through history one day at a time. Day j is the trading day, day j-1 is the news day.
      * decide with day j-1 numbers only (z-score, volume ratio, ATR)
      * enter at day j's open, exit at day j's close or at the stop, never carry overnight
    """
    slip = cfg["slippage"] if slippage is None else slippage
    idx = mk.idx
    lo = idx.searchsorted(pd.Timestamp(start))
    hi = idx.searchsorted(pd.Timestamp(end), side="right") - 1
    eq, rows, E = [], [], float(cfg["capital"])
    for j in range(max(lo, 1), hi + 1):
        cands = []
        for i in range(len(mk.names)):
            z, vr, atr = mk.Z[i, j - 1], mk.VR[i, j - 1], mk.ATR[i, j - 1]
            if np.isnan(z) or np.isnan(vr) or np.isnan(atr) or mk.quar[i, j - 1] or vr < m:
                continue
            if np.isnan(mk.O[i, j]) or np.isnan(mk.C[i, j]):
                continue
            if z >= k:
                cands.append((abs(z), i, +1))
            elif z <= -k:
                cands.append((abs(z), i, -1))
        cands.sort(reverse=True)                                  # biggest news first
        day_pnl = 0.0
        for _, i, d in cands[: cfg["max_positions"]]:
            o, h, l, c = mk.O[i, j], mk.H[i, j], mk.L[i, j], mk.C[i, j]
            fill = lambda p: min(max(p, l), h)                    # fills stay inside the day's range
            entry = fill(o * (1 + slip * d))                      # buyer pays up, short seller gets less
            dist = cfg["stop_atr"] * mk.ATR[i, j - 1]
            stop = entry - d * dist
            qty = math.floor(min(cfg["risk_per_trade"] * E / dist, cfg["max_pos_frac"] * E / entry))
            if qty <= 0 or stop <= 0:
                continue
            hit = (l <= stop) if d == 1 else (h >= stop)
            if hit:
                exit_, reason = fill(stop * (1 - slip * d)), "stop"
            else:
                exit_, reason = fill(c * (1 - slip * d)), "close"
            gross = qty * (exit_ - entry) * d
            buy_val = qty * (entry if d == 1 else exit_)
            sell_val = qty * (exit_ if d == 1 else entry)
            cost = (leg_cost(buy_val, "buy") + leg_cost(sell_val, "sell")) if charges else 0.0
            net = gross - cost
            day_pnl += net
            rows.append(dict(signal_date=idx[j - 1], date=idx[j], ticker=mk.names[i],
                             side="long" if d == 1 else "short", qty=qty, entry=entry, exit=exit_,
                             reason=reason, gross=gross, cost=cost, net=net,
                             ret_pct=net / (qty * entry) * 100, z=mk.Z[i, j - 1], vr=mk.VR[i, j - 1],
                             notional=qty * entry, equity_before=E))
        E += day_pnl
        eq.append((idx[j], E))
    equity = pd.Series([v for _, v in eq], index=[t for t, _ in eq])
    equity = pd.concat([pd.Series([float(cfg["capital"])], index=[idx[max(lo, 1) - 1]]), equity])
    trades = pd.DataFrame(rows)
    # sanity checks that would catch look-ahead or accounting slips
    if len(trades):
        assert (trades["signal_date"] < trades["date"]).all(), "signal must come before the trade"
        assert abs(equity.iloc[-1] - cfg["capital"] - trades["net"].sum()) < 1e-6, "equity does not match trade log"
        assert (trades["notional"] <= cfg["max_pos_frac"] * trades["equity_before"] + 1e-6).all(), "position too big"
    return equity, trades


# ----------------------------------------------------------------------------------------------
# STEP 5: PERFORMANCE NUMBERS
# ----------------------------------------------------------------------------------------------
def series_metrics(level, cfg):
    """
    Return metrics from a value series (equity curve or index level).
    Daily returns, 252-day year.  Sharpe = mean(excess) / std(excess) x sqrt(252).
    Sortino = mean(excess) / downside deviation x sqrt(252), downside deviation measured below the risk-free rate.
    """
    r = level.pct_change().dropna()
    n = len(r)
    cum = level.iloc[-1] / level.iloc[0] - 1

    def ratios(rf):
        ex = r - ((1 + rf) ** (1 / TRADING_DAYS) - 1)
        dd_dev = math.sqrt((np.minimum(ex, 0) ** 2).mean())
        sh = (ex.mean() / ex.std(ddof=1) * math.sqrt(TRADING_DAYS)) if ex.std(ddof=1) > 0 else None
        so = (ex.mean() / dd_dev * math.sqrt(TRADING_DAYS)) if dd_dev > 0 else None
        return sh, so

    sh, so = ratios(cfg["rf"])
    sh2, so2 = ratios(cfg["rf_alt"])
    return dict(
        days=n, cum_return=cum, ann_return=(1 + cum) ** (TRADING_DAYS / n) - 1,
        sharpe=sh, sortino=so, sharpe_alt=sh2, sortino_alt=so2,
        max_drawdown=float((level / level.cummax() - 1).min()),
    )


def trade_metrics(tr):
    if tr is None or len(tr) == 0:
        return dict(trades=0)
    w, l = tr[tr["net"] > 0], tr[tr["net"] <= 0]
    gp, gl = w["net"].sum(), -l["net"].sum()
    top5 = tr["net"].nlargest(5).sum()
    return dict(
        trades=int(len(tr)), longs=int((tr["side"] == "long").sum()), shorts=int((tr["side"] == "short").sum()),
        stops=int((tr["reason"] == "stop").sum()),
        win_rate=len(w) / len(tr), profit_factor=(gp / gl) if gl > 0 else None,
        avg_profit=float(w["net"].mean()) if len(w) else None, avg_profit_pct=float(w["ret_pct"].mean()) if len(w) else None,
        avg_loss=float(l["net"].mean()) if len(l) else None, avg_loss_pct=float(l["ret_pct"].mean()) if len(l) else None,
        max_profit=float(tr["net"].max()), max_profit_pct=float(tr["ret_pct"].max()),
        max_loss=float(tr["net"].min()), max_loss_pct=float(tr["ret_pct"].min()),
        net_pnl=float(tr["net"].sum()), gross_pnl=float(tr["gross"].sum()), costs=float(tr["cost"].sum()),
        top5_share_of_gross_profit=(top5 / gp) if gp > 0 else None,
        avg_trade_pct=float(tr["ret_pct"].mean()),
        # t-statistic of the average net trade return (is it distinguishable from zero?)
        avg_trade_t=(float(tr["ret_pct"].mean() / (tr["ret_pct"].std(ddof=1) / math.sqrt(len(tr))))
                     if len(tr) > 2 and tr["ret_pct"].std(ddof=1) > 0 else None),
    )


def evaluate(mk, cfg, k, m, start, end, **kw):
    eq, tr = run_backtest(mk, cfg, k, m, start, end, **kw)
    bench = mk.bench.loc[eq.index[0]: eq.index[-1]]          # same dates; first value = close before period
    sm = series_metrics(eq, cfg)
    out = dict(strategy=sm, benchmark=series_metrics(bench, cfg),
               trades=trade_metrics(tr), start=str(eq.index[1].date()), end=str(eq.index[-1].date()),
               active_days=float(tr["date"].nunique() / sm["days"]) if len(tr) else 0.0)
    return out, eq, tr


# ----------------------------------------------------------------------------------------------
# STEP 6: DIAGNOSTICS
# ----------------------------------------------------------------------------------------------
def event_study(mk, k, m, start, end):
    """Forget costs and sizing. On the session after a news day, how did open->close move in the news direction?"""
    idx = mk.idx
    lo, hi = idx.searchsorted(pd.Timestamp(start)), idx.searchsorted(pd.Timestamp(end), side="right") - 1
    out = {"bullish": [], "bearish": [], "all": []}
    for j in range(max(lo, 1), hi + 1):
        for i in range(len(mk.names)):
            o, c = mk.O[i, j], mk.C[i, j]
            if np.isnan(o) or np.isnan(c):
                continue
            r = c / o - 1
            out["all"].append(r)
            z, vr = mk.Z[i, j - 1], mk.VR[i, j - 1]
            if np.isnan(z) or np.isnan(vr) or mk.quar[i, j - 1] or vr < m:
                continue
            if z >= k:
                out["bullish"].append(r)
            elif z <= -k:
                out["bearish"].append(-r)                        # sign flipped: positive = the short made money
    res = {}
    for key, v in out.items():
        v = np.array(v)
        res[key] = dict(n=int(len(v)), mean_pct=float(v.mean() * 100) if len(v) else None,
                        se_pct=float(v.std(ddof=1) / math.sqrt(len(v)) * 100) if len(v) > 1 else None)
    return res


def regime_table(mk, tr, cfg):
    """Rising / falling / sideways by the index's trailing 60-day return at the close before the trade."""
    b60 = mk.bench.pct_change(60).shift(1)
    lab = pd.Series(np.where(b60 > 0.05, "rising", np.where(b60 < -0.05, "falling", "sideways")), index=b60.index)
    lab[b60.isna()] = "sideways"
    out = {}
    for name in ["rising", "falling", "sideways"]:
        t = tr[tr["date"].map(lab) == name]
        out[name] = dict(trades=int(len(t)), win_rate=float((t["net"] > 0).mean()) if len(t) else None,
                         avg_trade_pct=float(t["ret_pct"].mean()) if len(t) else None,
                         net_pnl=float(t["net"].sum()) if len(t) else 0.0)
    return out


def per_stock_table(tr):
    out = {}
    for name, t in tr.groupby("ticker"):
        out[name] = dict(trades=int(len(t)), longs=int((t["side"] == "long").sum()), shorts=int((t["side"] == "short").sum()),
                         win_rate=float((t["net"] > 0).mean()), net_pnl=float(t["net"].sum()))
    return out


def tune(mk, cfg, with_test=True):
    """Pick (k, m) on the DEVELOPMENT period only, by Sharpe ratio. The test column is shown for robustness, never used to choose."""
    rows = []
    for k in cfg["grid_k"]:
        for m in cfg["grid_m"]:
            dev, _, _ = evaluate(mk, cfg, k, m, cfg["dev_start"], cfg["dev_end"])
            row = dict(k=k, m=m, dev_trades=dev["trades"]["trades"], dev_cum=dev["strategy"]["cum_return"],
                       dev_sharpe=dev["strategy"]["sharpe"])
            if with_test:
                tst, _, _ = evaluate(mk, cfg, k, m, cfg["test_start"], cfg["test_end"])
                row.update(test_trades=tst["trades"]["trades"], test_cum=tst["strategy"]["cum_return"],
                           test_sharpe=tst["strategy"]["sharpe"])
            rows.append(row)
    ok = [r for r in rows if r["dev_trades"] >= cfg["min_dev_trades"] and r["dev_sharpe"] is not None]
    best = max(ok, key=lambda r: r["dev_sharpe"])
    return rows, best


# ----------------------------------------------------------------------------------------------
# STEP 7: CHARTS
# ----------------------------------------------------------------------------------------------
BLUE, ORANGE, INK, INK2, MUTED = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#898781"
GRID, BASE, SURF = "#e1e0d9", "#c3c2b7", "#fcfcfb"


def chart_style():
    fam = "Inter" if any(f.name == "Inter" for f in font_manager.fontManager.ttflist) else "DejaVu Sans"
    plt.rcParams.update({
        "font.family": fam, "font.size": 9, "text.color": INK, "axes.labelcolor": INK2,
        "axes.edgecolor": BASE, "axes.facecolor": SURF, "figure.facecolor": SURF,
        "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
        "legend.frameon": False, "savefig.dpi": 200, "lines.linewidth": 2.0, "lines.solid_capstyle": "round",
    })


def fig_prices(mk, cfg, path, label):
    fig, axes = plt.subplots(2, 4, figsize=(10, 4.6), sharex=True)
    t0 = pd.Timestamp(cfg["test_start"])
    for ax, name in zip(axes.ravel(), mk.names):
        i = mk.names.index(name)
        ax.plot(mk.idx, mk.C[i], color=BLUE, lw=1.4)
        ax.axvline(t0, color=BASE, lw=1)
        ax.set_title(name, loc="left", fontsize=9, color=INK2)
        ax.tick_params(labelsize=7.5)
    fig.suptitle(f"Daily closing prices, {label}. The grey vertical line marks the start of the test period.",
                 x=0.01, ha="left", fontsize=10, color=INK)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_events(ev_dev, ev_test, friction_pct, path):
    fig, ax = plt.subplots(figsize=(6.6, 3.4))
    groups = [("Development", ev_dev), ("Test", ev_test)]
    w = 0.32
    for gi, (gname, ev) in enumerate(groups):
        for bi, (key, color, lab) in enumerate([("bullish", BLUE, "Good-news day, then long"),
                                                ("bearish", ORANGE, "Bad-news day, then short")]):
            e = ev[key]
            x = gi + (bi - 0.5) * (w + 0.04)
            if e["n"] == 0 or e["mean_pct"] is None:
                continue
            ax.bar(x, e["mean_pct"], w, color=color, label=lab if gi == 0 else None)
            ax.errorbar(x, e["mean_pct"], yerr=1.96 * (e["se_pct"] or 0), color=INK2, lw=1, capsize=3)
            ax.text(x, (e["mean_pct"] + 1.96 * (e["se_pct"] or 0)) + 0.01, f"{e['mean_pct']:+.2f}%\nn={e['n']}",
                    ha="center", va="bottom", fontsize=7.5, color=INK2)
    ax.axhline(0, color=BASE, lw=1)
    ax.axhline(friction_pct, color=MUTED, lw=1)
    ax.text(1.62, friction_pct, " round-trip\n cost + slippage", fontsize=7, color=MUTED, va="center")
    ax.set_xticks([0, 1])
    ax.set_xticklabels([g[0] for g in groups])
    ax.set_xlim(-0.6, 1.9)
    ax.set_ylabel("Mean open-to-close return in the trade direction (%)")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_equity(eq_dev, b_dev, eq_test, b_test, path):
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.5))
    for ax, e, b, title in [(axes[0], eq_dev, b_dev, "Development period"), (axes[1], eq_test, b_test, "Test period")]:
        e100, b100 = e / e.iloc[0] * 100, b / b.iloc[0] * 100
        ax.plot(b100.index, b100, color=ORANGE, label="Nifty (benchmark)")
        ax.plot(e100.index, e100, color=BLUE, label="Strategy")
        ax.set_title(title, loc="left", fontsize=9.5, color=INK2)
        ax.set_ylabel("Value, start = 100")
        ax.text(e100.index[-1], e100.iloc[-1], f" {e100.iloc[-1]:.0f}", color=INK2, fontsize=8, va="center")
        ax.text(b100.index[-1], b100.iloc[-1], f" {b100.iloc[-1]:.0f}", color=INK2, fontsize=8, va="center")
        ax.margins(x=0.08)
    axes[0].legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_drawdown(eq, b, path):
    fig, ax = plt.subplots(figsize=(8, 3.1))
    for s, color, lab in [(b, ORANGE, "Nifty (benchmark)"), (eq, BLUE, "Strategy")]:
        dd = (s / s.cummax() - 1) * 100
        ax.fill_between(dd.index, dd, 0, color=color, alpha=0.10, lw=0)
        ax.plot(dd.index, dd, color=color, label=lab, lw=1.8)
    ax.set_ylabel("Drop from previous peak (%)")
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_trades(mk, tr, path, zoom=60):
    """Top: the whole test period for the stock with the most trades. Bottom: a zoom on its busiest stretch,
    with each day's high-low range so the same-day entry and exit are visible."""
    name = tr["ticker"].value_counts().index[0]
    i = mk.names.index(name)
    t = tr[tr["ticker"] == name].sort_values("date")
    pos = mk.idx.searchsorted(t["date"])
    a = max(range(len(pos)), key=lambda q: int(((pos >= pos[q]) & (pos < pos[q] + zoom)).sum()))
    w0 = max(0, pos[a] - 12)
    w1 = min(len(mk.idx), w0 + zoom)
    first = mk.idx.searchsorted(pd.Timestamp(t["date"].min())) - 5
    full = slice(max(0, first), mk.idx.searchsorted(pd.Timestamp(t["date"].max())) + 5)

    fig, (ax, az) = plt.subplots(2, 1, figsize=(10, 6.4), gridspec_kw={"height_ratios": [1, 1.1]})
    ax.plot(mk.idx[full], mk.C[i][full], color=MUTED, lw=1.3, label="Close")
    ax.axvspan(mk.idx[w0], mk.idx[w1 - 1], color=BASE, alpha=0.35, lw=0)
    ax.set_ylabel(f"{name} price (Rs)")
    ax.set_title("Whole test period (shaded stretch is enlarged below)", loc="left", fontsize=9, color=INK2)

    xz = mk.idx[w0:w1]
    az.vlines(xz, mk.L[i][w0:w1], mk.H[i][w0:w1], color=BASE, lw=1.4, label="Day's high-low range")
    az.plot(xz, mk.C[i][w0:w1], color=MUTED, lw=1.3, label="Close")
    az.set_ylabel(f"{name} price (Rs)")
    az.set_title("Enlarged stretch", loc="left", fontsize=9, color=INK2)

    for axis, zoomed in [(ax, False), (az, True)]:
        sub = t[(t["date"] >= mk.idx[w0]) & (t["date"] < mk.idx[w1 - 1])] if zoomed else t
        for side, color, marker, lab in [("long", BLUE, "^", "Long entry (open)"), ("short", ORANGE, "v", "Short entry (open)")]:
            s = sub[sub["side"] == side]
            axis.scatter(s["date"], s["entry"], marker=marker, s=70 if zoomed else 38, color=color,
                         edgecolor=SURF, linewidth=1.2, zorder=6, label=lab)
        if zoomed:
            for _, r in sub.iterrows():
                axis.plot([r["date"], r["date"]], [r["entry"], r["exit"]], color=BLUE if r["side"] == "long" else ORANGE, lw=2, zorder=3)
            for reason, marker, lab in [("close", "o", "Exit at close"), ("stop", "X", "Exit at stop-loss")]:
                s = sub[sub["reason"] == reason]
                axis.scatter(s["date"], s["exit"], marker=marker, s=44, facecolor=SURF if reason == "close" else INK,
                             edgecolor=INK, linewidth=1.1, zorder=5, label=lab)
    handles, labels = az.get_legend_handles_labels()
    az.legend(handles, labels, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.12), fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return name


def fig_trade_dist(tr, path):
    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.0), sharey=True, sharex=True)
    lo, hi = np.floor(tr["ret_pct"].min()), np.ceil(tr["ret_pct"].max())
    bins = np.linspace(lo, hi, 31)
    for ax, side, color, title in [(axes[0], "long", BLUE, "Long trades"), (axes[1], "short", ORANGE, "Short trades")]:
        v = tr.loc[tr["side"] == side, "ret_pct"]
        ax.hist(v, bins=bins, color=color)
        ax.axvline(0, color=BASE, lw=1)
        if len(v):
            ax.axvline(v.mean(), color=INK, lw=1.4)
            ax.text(v.mean(), ax.get_ylim()[1] * 0.97, f" mean {v.mean():+.2f}%", fontsize=8, color=INK2, va="top")
        ax.set_title(f"{title} (n={len(v)})", loc="left", fontsize=9, color=INK2)
        ax.set_xlabel("Net return per trade (%)")
    axes[0].set_ylabel("Number of trades")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ----------------------------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------------------------
def native(o):
    if isinstance(o, dict):
        return {str(k): native(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [native(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (isinstance(o, float) and math.isnan(o)) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


def build_market(source, cfg, drift_frac=0.10):
    if source == "synthetic":
        panel, bench, splits = make_synthetic(cfg, drift_frac)
    else:
        panel, bench, splits = load_yfinance(cfg)
    panel, rep = clean(panel, bench)
    ind = {n: add_indicators(d, cfg) for n, d in panel.items()}
    return Market(ind, bench, splits, cfg), rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["synthetic", "yfinance"], default="synthetic")
    ap.add_argument("--out", default="outputs")
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "figs"), exist_ok=True)
    cfg = deepcopy(CFG)
    chart_style()

    mk, cleaning = build_market(a.source, cfg)
    label = "synthetic data (not real prices)" if a.source == "synthetic" else "NSE data via yfinance"

    # development: choose thresholds ------------------------------------------------------
    grid, best = tune(mk, cfg)
    k, m = best["k"], best["m"]
    print(f"Chosen on development data: k={k}, m={m}  (dev Sharpe {best['dev_sharpe']:.2f}, {best['dev_trades']} trades)")

    dev, eq_dev, tr_dev = evaluate(mk, cfg, k, m, cfg["dev_start"], cfg["dev_end"])
    tst, eq_tst, tr_tst = evaluate(mk, cfg, k, m, cfg["test_start"], cfg["test_end"])   # the one test run
    gross, _, _ = evaluate(mk, cfg, k, m, cfg["test_start"], cfg["test_end"], slippage=0.0, charges=False)
    sens = {}
    for s in [0.0, 0.0005, 0.001, 0.002]:
        r, _, _ = evaluate(mk, cfg, k, m, cfg["test_start"], cfg["test_end"], slippage=s)
        sens[str(s)] = dict(cum=r["strategy"]["cum_return"], sharpe=r["strategy"]["sharpe"], trades=r["trades"]["trades"])

    ev_dev = event_study(mk, k, m, cfg["dev_start"], cfg["dev_end"])
    ev_tst = event_study(mk, k, m, cfg["test_start"], cfg["test_end"])
    friction = float((tr_tst["cost"] / tr_tst["notional"]).mean() * 100 + 2 * cfg["slippage"] * 100)

    # placebo: same random numbers, planted continuation switched off (synthetic only) ----
    placebo = None
    if a.source == "synthetic":
        mk0, _ = build_market("synthetic", cfg, drift_frac=0.0)
        _, best0 = tune(mk0, cfg, with_test=False)
        t0, _, _ = evaluate(mk0, cfg, best0["k"], best0["m"], cfg["test_start"], cfg["test_end"])
        e0 = event_study(mk0, best0["k"], best0["m"], cfg["test_start"], cfg["test_end"])
        placebo = dict(k=best0["k"], m=best0["m"], dev_sharpe=best0["dev_sharpe"], test=t0, events=e0)

    # charts ---------------------------------------------------------------------------------
    f = lambda n: os.path.join(a.out, "figs", n)
    fig_prices(mk, cfg, f("fig1_prices.png"), label)
    fig_events(ev_dev, ev_tst, friction, f("fig2_events.png"))
    fig_equity(eq_dev, mk.bench.loc[eq_dev.index[0]: eq_dev.index[-1]], eq_tst, mk.bench.loc[eq_tst.index[0]: eq_tst.index[-1]], f("fig3_equity.png"))
    fig_drawdown(eq_tst, mk.bench.loc[eq_tst.index[0]: eq_tst.index[-1]], f("fig4_drawdown.png"))
    shown = fig_trades(mk, tr_tst, f("fig5_trades.png"))
    fig_trade_dist(tr_tst, f("fig6_trade_dist.png"))

    tr_dev.to_csv(os.path.join(a.out, "trades_dev.csv"), index=False)
    tr_tst.to_csv(os.path.join(a.out, "trades_test.csv"), index=False)

    results = dict(
        source=a.source, label=label, cfg=cfg, cleaning=cleaning, grid=grid, chosen=dict(k=k, m=m),
        dev=dev, test=tst, test_gross=gross, sensitivity=sens, events_dev=ev_dev, events_test=ev_tst,
        friction_pct=friction, regimes_dev=regime_table(mk, tr_dev, cfg), regimes_test=regime_table(mk, tr_tst, cfg),
        per_stock_test=per_stock_table(tr_tst), placebo=placebo, trades_chart_stock=shown,
        sides_test={s: dict(trades=int(len(g)), net_pnl=float(g["net"].sum()), avg_trade_pct=float(g["ret_pct"].mean()),
                            win_rate=float((g["net"] > 0).mean())) for s, g in tr_tst.groupby("side")},
        test_exit_reasons=tr_tst["reason"].value_counts().to_dict(),
    )
    with open(os.path.join(a.out, "results.json"), "w") as fh:
        json.dump(native(results), fh, indent=2)
    print("Development:", json.dumps(native(dev["strategy"]), indent=None))
    print("Test       :", json.dumps(native(tst["strategy"]), indent=None))
    print("Test trades:", json.dumps(native(tst["trades"]), indent=None))
    print("Nifty test :", json.dumps(native(tst["benchmark"]), indent=None))


if __name__ == "__main__":
    main()
