# PS2 – Trading the day after the news (CFA Club induction task)

An intraday long/short backtest on Indian large-cap stocks, plus the script that builds the PDF report.

**Idea in one line:** after a day with an unusually big move on unusually heavy volume, trade the next session in the same direction, long after good news, short after bad news, and square off the same day (shorting in the cash segment has to be closed intraday).

## Files

| File | What it does |
|---|---|
| `news_intraday_strategy.py` | Gets data, flags news days, runs the backtest, tunes on the development period, runs the test period once, writes metrics, trade logs and charts |
| `build_report.py` | Reads the results and the charts and writes the PDF report |
| `CFA_PS2_Sample_Report.pdf` | Sample report. **Built on synthetic prices**, so the numbers mean nothing for real stocks |
| `outputs/trades_dev.csv`, `outputs/trades_test.csv` | Every trade, one row each |

## Setup

Python 3.10 or newer.

```
pip install numpy pandas matplotlib reportlab
pip install yfinance        # only needed for real data
```

`reportlab` needs Pillow and fontTools, which install with matplotlib/reportlab.

## Run

Synthetic data (works offline, about 10 seconds):

```
python news_intraday_strategy.py --source synthetic --out outputs
python build_report.py --results outputs/results.json --out CFA_PS2_Sample_Report.pdf
```

Real NSE data (needs internet):

```
python news_intraday_strategy.py --source yfinance --out outputs_real
python build_report.py --results outputs_real/results.json --out PS2_Report_Real.pdf
```

The yfinance path could not be tested where this sample was built (no internet). If it fails, check the printed error first: symbol names end in `.NS`, and some yfinance versions return a two-level column index (the loader already flattens it).

## Settings

Everything you can change is in the `CFG` dictionary at the top of `news_intraday_strategy.py`: stock list, date ranges, development/test split, capital, stop distance, risk per trade, position cap, slippage, risk-free rate, and the grid of thresholds to try.

## How the backtest avoids cheating

- The signal for day *t* uses only data up to the close of day *t*. The trade happens at the open of day *t+1*. The code asserts this.
- Settings are chosen on 2016–2021 only (highest Sharpe among settings with at least 30 trades). 2022 onwards is run once.
- Fills are clipped to the day's high–low range, and the code checks that equity equals starting capital plus the sum of net trade P&L.
- Zerodha equity-intraday charges are applied to every order, plus 0.05% slippage per fill.

## For real data

1. Fix the stock list, date ranges, grid and metrics **before** looking at test-period results. Changing them afterwards turns the test period into a second development period.
2. Run once, report whatever comes out, including weak results.
3. Rewrite the report text in your own words and make sure you can explain every table. The brief asks for all sources and assistance to be acknowledged, so keep the acknowledgement section accurate.

## Sources

Zerodha Varsity (Modules 1, 2 and 9) and Zerodha's charges page for the cost model; Wilder (1978) for ATR; Sharpe (1994); Sortino and Price (1994). Full list with links is in section 9 of the report.
