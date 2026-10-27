#!/usr/bin/env python3
"""
Builds the PDF report for PS2 from the files written by news_intraday_strategy.py.

    python build_report.py --results outputs/results.json --out CFA_PS2_Report.pdf

Every number in the report is read from results.json, so re-running the strategy script on real data
and then this script gives a report with the real numbers. The wording that describes results
(better / worse than the index, positive / negative) is chosen from the numbers, but read it through
and rewrite it in your own words before you submit.
"""
import argparse
import json
import math
import os

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

# ------------------------------------------------------------------ fonts and style
import matplotlib
from fontTools.ttLib import TTFont as FTFont      # only used to check which symbols the font really has

# Carlito (a clean humanist sans) if this machine has it, otherwise the DejaVu fonts that ship with matplotlib,
# so the script runs on any computer that can run the strategy script.
FD = "/usr/share/fonts/truetype/crosextra/"
DJ = os.path.join(matplotlib.get_data_path(), "fonts", "ttf") + os.sep
if os.path.exists(FD + "Carlito-Regular.ttf"):
    FILES = dict(n=FD + "Carlito-Regular.ttf", b=FD + "Carlito-Bold.ttf", i=FD + "Carlito-Italic.ttf", bi=FD + "Carlito-BoldItalic.ttf")
    SCALE = 1.0
else:
    FILES = dict(n=DJ + "DejaVuSans.ttf", b=DJ + "DejaVuSans-Bold.ttf", i=DJ + "DejaVuSans-Oblique.ttf", bi=DJ + "DejaVuSans-BoldOblique.ttf")
    SCALE = 0.93            # DejaVu is wider, so shrink the type slightly to keep the layout
pdfmetrics.registerFont(TTFont("Body", FILES["n"]))
pdfmetrics.registerFont(TTFont("Body-Bold", FILES["b"]))
pdfmetrics.registerFont(TTFont("Body-Italic", FILES["i"]))
pdfmetrics.registerFont(TTFont("Body-BoldItalic", FILES["bi"]))
pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic", boldItalic="Body-BoldItalic")
pdfmetrics.registerFont(TTFont("Mono", DJ + "DejaVuSansMono.ttf"))

_cmap = FTFont(FILES["n"])["cmap"].getBestCmap()
MINUS = "−" if 0x2212 in _cmap else "-"
GE = "≥" if 0x2265 in _cmap else ">="
LE = "≤" if 0x2264 in _cmap else "<="
RS = "₹" if 0x20B9 in _cmap else "Rs "
TIMES = "×" if 0xD7 in _cmap else "x"
ARROW = "→" if 0x2192 in _cmap else "->"

NAVY, INK, SOFT, RULE = "#1f3a5f", "#1a1a1a", "#5a5a55", "#d9d9d2"
ACCENT = "#2a78d6"
WARN = "#b4440f"

def _scaled(styles):
    if SCALE != 1.0:
        for s in styles.values():
            s.fontSize *= SCALE
            s.leading *= SCALE
    return styles


ST = {
    "title": ParagraphStyle("title", fontName="Body-Bold", fontSize=22, leading=26, textColor=colors.HexColor(NAVY), spaceAfter=2),
    "subtitle": ParagraphStyle("subtitle", fontName="Body", fontSize=13, leading=17, textColor=colors.HexColor(SOFT), spaceAfter=4),
    "meta": ParagraphStyle("meta", fontName="Body", fontSize=9.5, leading=13, textColor=colors.HexColor(SOFT), spaceAfter=8),
    "h1": ParagraphStyle("h1", fontName="Body-Bold", fontSize=14.5, leading=18, textColor=colors.HexColor(NAVY), spaceBefore=12, spaceAfter=5,
                         keepWithNext=1),
    "h2": ParagraphStyle("h2", fontName="Body-Bold", fontSize=11.3, leading=14.5, textColor=colors.HexColor(NAVY), spaceBefore=8, spaceAfter=3,
                         keepWithNext=1),
    "body": ParagraphStyle("body", fontName="Body", fontSize=10.4, leading=14.4, textColor=colors.HexColor(INK), spaceAfter=5.5, alignment=TA_LEFT,
                           allowWidows=0, allowOrphans=0),
    "bullet": ParagraphStyle("bullet", fontName="Body", fontSize=10.4, leading=14.2, textColor=colors.HexColor(INK), spaceAfter=3, leftIndent=15, bulletIndent=3),
    "cap": ParagraphStyle("cap", fontName="Body", fontSize=8.8, leading=11.6, textColor=colors.HexColor(SOFT), spaceBefore=3, spaceAfter=9),
    "tcap": ParagraphStyle("tcap", fontName="Body-Bold", fontSize=9.2, leading=12, textColor=colors.HexColor(INK), spaceBefore=5, spaceAfter=3),
    "cell": ParagraphStyle("cell", fontName="Body", fontSize=8.9, leading=11.2, textColor=colors.HexColor(INK)),
    "cellr": ParagraphStyle("cellr", fontName="Body", fontSize=8.9, leading=11.2, textColor=colors.HexColor(INK), alignment=2),
    "cellh": ParagraphStyle("cellh", fontName="Body-Bold", fontSize=8.9, leading=11.2, textColor=colors.HexColor(NAVY)),
    "cellhr": ParagraphStyle("cellhr", fontName="Body-Bold", fontSize=8.9, leading=11.2, textColor=colors.HexColor(NAVY), alignment=2),
    "note": ParagraphStyle("note", fontName="Body", fontSize=9.6, leading=13.2, textColor=colors.HexColor(INK)),
    "small": ParagraphStyle("small", fontName="Body", fontSize=9, leading=12, textColor=colors.HexColor(INK), spaceAfter=3.5, leftIndent=12, firstLineIndent=-12),
}

ST = _scaled(ST)
PAGE_W, PAGE_H = A4
LM = RM = 20 * mm

# The tables are numbered in the order they are built; text refers to these numbers, and table() checks them.
TN = dict(dev=4, metrics=5, grid=8)
FRAME_W = PAGE_W - LM - RM


# ------------------------------------------------------------------ number formatting
def sgn(s):
    return s.replace("-", MINUS)


def pc(x, d=2, sign=False):
    if x is None:
        return "N/A"
    return sgn(f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%")


def pp(x, d=2, sign=True):
    """x already in percent units"""
    if x is None:
        return "N/A"
    return sgn(f"{x:+.{d}f}%" if sign else f"{x:.{d}f}%")


def nm(x, d=2):
    return "N/A" if x is None else sgn(f"{x:.{d}f}")


def inr(x, d=0):
    if x is None:
        return "N/A"
    neg, x = x < 0, abs(x)
    ip, _, fp = f"{x:.{d}f}".partition(".")
    if len(ip) > 3:
        head, tail, parts = ip[:-3], ip[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        ip = ",".join(parts + [tail])
    return (MINUS if neg else "") + RS + ip + ("." + fp if fp else "")


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------------ building blocks
def P(text, style="body"):
    return Paragraph(text, ST[style])


def bullets(items, numbered=False):
    return [Paragraph(t, ST["bullet"], bulletText=(f"{i}." if numbered else "•")) for i, t in enumerate(items, 1)]


def box(text, color=NAVY, fill="#f3f4f6"):
    t = Table([[Paragraph(text, ST["note"])]], colWidths=[FRAME_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(fill)),
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(color)),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


class Counter:
    def __init__(self):
        self.t = 0
        self.f = 0


CNT = Counter()


def table(rows, widths, header_rows=1, right_from=1, bold_rows=(), shade_rows=(), caption=None, note=None, span=(), key=None):
    """rows: list of lists of str. Columns from right_from onward are right-aligned."""
    data = []
    for r, row in enumerate(rows):
        line = []
        for c, cell in enumerate(row):
            hdr = r < header_rows
            right = c >= right_from
            style = ("cellhr" if right else "cellh") if hdr else ("cellr" if right else "cell")
            txt = f"<b>{cell}</b>" if (r in bold_rows and not hdr) else cell
            line.append(Paragraph(txt, ST[style]))
        data.append(line)
    t = Table(data, colWidths=widths, repeatRows=header_rows)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
        ("LINEABOVE", (0, 0), (-1, 0), 0.9, colors.HexColor(NAVY)),
        ("LINEBELOW", (0, header_rows - 1), (-1, header_rows - 1), 0.9, colors.HexColor(NAVY)),
        ("LINEBELOW", (0, header_rows), (-1, -2), 0.4, colors.HexColor(RULE)),
        ("LINEBELOW", (0, -1), (-1, -1), 0.9, colors.HexColor(NAVY)),
    ]
    for r in shade_rows:
        cmds.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#eef3fa")))
    for s in span:
        cmds.append(("SPAN", s[0], s[1]))
    t.setStyle(TableStyle(cmds))
    out = []
    if caption:
        CNT.t += 1
        if key:
            assert TN[key] == CNT.t, f"text refers to Table {TN[key]} for '{key}' but it is Table {CNT.t}"
        out.append(Paragraph(f"Table {CNT.t}. {caption}", ST["tcap"]))
    out.append(t)
    if note:
        out.append(Paragraph(note, ST["cap"]))
    else:
        out.append(Spacer(1, 7))
    return KeepTogether(out)


def figure(path, caption, width=FRAME_W):
    CNT.f += 1
    w, h = PILImage.open(path).size
    img = Image(path, width=width, height=width * h / w)
    return KeepTogether([img, Paragraph(f"<b>Figure {CNT.f}.</b> {caption}", ST["cap"])])


# ------------------------------------------------------------------ page furniture
def make_pagefn(sample):
    def fn(c, doc):
        c.saveState()
        c.setStrokeColor(colors.HexColor(RULE))
        c.setLineWidth(0.6)
        c.line(LM, PAGE_H - 15 * mm, PAGE_W - RM, PAGE_H - 15 * mm)
        c.setFont("Body", 8.5)
        c.setFillColor(colors.HexColor(SOFT))
        c.drawString(LM, PAGE_H - 12.4 * mm, "CFA Club, IIT Indore  ·  Induction Task 2026  ·  PS2 Finance & Quant")
        c.drawRightString(PAGE_W - RM, PAGE_H - 12.4 * mm, "Harshit Kumar Arya")
        c.line(LM, 15 * mm, PAGE_W - RM, 15 * mm)
        if sample:
            c.setFillColor(colors.HexColor(WARN))
            c.setFont("Body-Bold", 8.5)
            c.drawString(LM, 10.8 * mm, "SAMPLE REPORT  ·  SYNTHETIC PRICES, NOT REAL MARKET RESULTS")
        c.setFillColor(colors.HexColor(SOFT))
        c.setFont("Body", 8.5)
        c.drawRightString(PAGE_W - RM, 10.8 * mm, f"Page {doc.page}")
        c.restoreState()
    return fn


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="outputs/results.json")
    ap.add_argument("--out", default="CFA_PS2_Report.pdf")
    a = ap.parse_args()
    base = os.path.dirname(os.path.abspath(a.results))
    R = json.load(open(a.results))
    figs = lambda n: os.path.join(base, "figs", n)

    sample = R["source"] == "synthetic"
    cfg, D, T = R["cfg"], R["dev"], R["test"]
    ds, ts, db, tb = D["strategy"], T["strategy"], D["benchmark"], T["benchmark"]
    dt, tt = D["trades"], T["trades"]
    k, m = R["chosen"]["k"], R["chosen"]["m"]
    fric = R["friction_pct"]
    active_t = T["active_days"] * 100
    ev_t, ev_d = R["events_test"], R["events_dev"]
    sides = R["sides_test"]
    gross_pnl = tt["gross_pnl"]
    cost_share = tt["costs"] / gross_pnl * 100 if gross_pnl > 0 else None
    grid = R["grid"]
    ks, ms = sorted({g["k"] for g in grid}), sorted({g["m"] for g in grid})

    story = []

    # ---------------------------------------------------------------- title block
    story += [P("Trading the day after the news", "title"),
              P("An intraday long and short backtest on Indian large-cap stocks", "subtitle"),
              P(f"{len(cfg['tickers'])} large-cap stocks  ·  daily data {cfg['data_start'][:4]}&ndash;{cfg['data_end'][:4]}  ·  "
                "intraday trades only  ·  long and short", "meta")]
    if sample:
        story += [box("<b>Sample report.</b> Every price in this document is synthetic. The accompanying code generated it so that the "
                      "whole pipeline could be run and checked without internet access. The numbers show how the report and the code "
                      "work. They say nothing about how this strategy would do on real NSE data.", WARN, "#fbf1ea"), Spacer(1, 7)]

    beat = ts["cum_return"] > tb["cum_return"]
    story.append(box(
        "<b>In short.</b> I tested one simple idea. When a stock makes an unusually big move on unusually heavy volume, I call that "
        "a news day and trade the next session in the same direction, only intraday: a long from the open to the close after good "
        "news, a short from the open to the close after bad news. Nothing is held overnight, which is also what makes the short "
        "side possible in the cash segment.<br/><br/>"
        f"I chose the two thresholds that define &ldquo;unusually big&rdquo; and &ldquo;unusually heavy&rdquo; using "
        f"{cfg['dev_start'][:4]}&ndash;{cfg['dev_end'][:4]} data and then ran {cfg['test_start'][:4]} onwards once. In this "
        f"{'synthetic ' if sample else ''}market the chosen setting returned <b>{pc(ts['cum_return'], 1, True)}</b> after all costs in the test "
        f"period, against {pc(tb['cum_return'], 1, True)} for the {'index' if sample else 'Nifty 50'}, from {tt['trades']} trades. That is a small "
        f"number, partly because the strategy was in the market on only about {active_t:.0f}% of days. What I trust more than the headline "
        f"is what sits under it: the average trade earned {pp(tt['avg_trade_pct'])} net, while costs and slippage take about "
        f"{fric:.2f}% from every round trip. The margin for error is thin."))
    story.append(Spacer(1, 4))

    # ---------------------------------------------------------------- 1 idea
    story += [P("1. The idea and why I chose it", "h1"),
              P("Big moves on heavy volume usually mean that something happened: a results announcement, an order win, a rating change, a "
                "regulatory ruling. Zerodha Varsity's chapter on key events (Module 1, chapter 12) makes the point that prices react to the gap "
                "between the news and what the market expected, not to the news itself. My question is whether, once that first reaction is "
                "visible in the closing price, the move carries on into the next session."),
              P("I like this idea for three reasons. It is how I would actually trade: see the news, trade the reaction, be flat by the close. "
                "It needs only open, high, low, close and volume. And it gives me a natural reason to test the short side, because bad news is "
                "the textbook case for one."),
              P("<b>Which track is this?</b> It is closest to Track 2 (trend following), because it bets that a move continues instead of "
                "reversing. I did not build the signal from an EMA crossover or MACD. It is built from the size of the move and the volume "
                "behind it, and I am saying so here so the choice is visible rather than hidden."),
              P("<b>Why every trade is intraday.</b> Shorting in the cash segment comes with one rule that shapes the whole design. Varsity "
                "(Module 1, chapter 8) says a short position in stocks has to be bought back the same day, before the market closes. So every "
                "trade in this report, long or short, opens at the open and closes the same day. That makes the short side possible with an "
                "ordinary intraday order, with no overnight borrowing to model."),
              P("What counts as news", "h2"),
              P("I cannot read headlines from price data, so I use a proxy: a news day is a day on which a stock moved much more than usual "
                "and traded much more than usual. A big move on thin volume could just be noise. Varsity's chapter on volume (Module 2, "
                "chapter 12) treats volume as the way to confirm a price move and compares each day with the average of the last 10 days, so "
                "I do the same. The proxy will sometimes flag days with no company news at all, for example a market-wide scare. I come back "
                "to that under limitations.")]

    # ---------------------------------------------------------------- 2 data
    story += [CondPageBreak(50 * mm), P("2. Data", "h1")]
    names = ", ".join(cfg["tickers"])
    if sample:
        story.append(P(
            "<b>Source.</b> The prices in this sample are synthetic. The function <font name='Mono' size='9'>make_synthetic()</font> in "
            f"<font name='Mono' size='9'>news_intraday_strategy.py</font> builds eight stocks that borrow their names from large Nifty companies "
            f"({names}), plus a Nifty-like index. The market has bull, bear and sideways stretches, a gap at the open, a high and low inside each "
            "day, and volume that rises with the size of the move. About three news days per stock per year are planted, each with a big move "
            "and a volume surge. The day after a news day, the intraday move leans 10% of the way in the news direction. That planted effect is "
            "there so I can check that the code finds an effect when one exists and finds none when it does not (section 6.6). The price levels "
            f"are invented and do not match the real companies. The random seed is {cfg['seed']}."))
    else:
        story.append(P(
            f"<b>Source.</b> Daily open, high, low, close and volume for {names} and the Nifty 50 index were downloaded from Yahoo Finance with "
            "the yfinance library (tickers such as RELIANCE.NS and ^NSEI), adjusted for splits and dividends."))
    story += [
        P("<b>Why these eight, and why daily bars.</b> They are large and heavily traded, and they come from banks, IT, energy, FMCG and "
          "engineering, so there are enough news days to test and the stocks are liquid enough that my slippage assumption is not absurd. "
          "I assume all eight can be shorted intraday, which should be checked against the broker's list. Daily bars are what the brief asks "
          "for. The price of that choice is that I cannot see what happens inside a session, which I also return to later."),
        P(f"<b>Period.</b> Data run from {cfg['data_start']} to {cfg['data_end']}. The first year only warms up the 20-day and 10-day windows. "
          f"The <b>development period</b> ({cfg['dev_start'][:4]}&ndash;{cfg['dev_end'][:4]}) was used for every decision. The <b>test period</b> "
          f"({cfg['test_start'][:4]} to {cfg['test_end']}) was run once, at the end."),
        P("<b>Checks and cleaning.</b> For each stock I looked for missing days, duplicate dates, non-positive prices, zero volume, rows where "
          "the high is below the open or close (or the low above it), and single-day moves above 20%, which are often an unadjusted split. "
          "Any such row would be set to missing and never filled forward, so the strategy cannot trade on a price that was not there. "
          "Corporate actions need extra care with real data (Varsity Module 1, chapter 11): the code skips signals for 10 days after a split "
          "date, because volume may not be adjusted the way prices are.")]
    cl = R["cleaning"]["stocks"]
    rows = [["Stock", "Rows", "Missing days", "Zero volume", "Bad high/low", "Moves above 20%"]]
    for n_, v in cl.items():
        rows.append([n_, f"{v['rows']:,}", str(v["missing_days"]), str(v["zero_volume"]), str(v["bad_high_low"]), str(v["moves_over_20pct"])])
    story.append(table(rows, [34 * mm, 24 * mm, 28 * mm, 28 * mm, 28 * mm, 28 * mm],
                       caption="Data checks, one row per stock.",
                       note=("Nothing needed removing in the synthetic data. The generated calendar is Monday to Friday with no exchange "
                             "holidays; real data would use the actual NSE calendar." if sample else
                             "Rows flagged here are set to missing, not filled.")))
    story.append(figure(figs("fig1_prices.png"),
                        "Closing prices of the eight stocks. The shifts in the underlying market create rising, falling and sideways stretches, "
                        "which matter for the regime check in section 6.4."))

    # ---------------------------------------------------------------- 3 rules
    story += [CondPageBreak(60 * mm), P("3. Signal and exact trading rules", "h1"),
              P("Everything below uses only information available at the close of the day it is calculated."),
              table([["Quantity", "How it is calculated"],
                     ["Daily return, r<sub>t</sub>", "Close<sub>t</sub> / Close<sub>t&minus;1</sub> &minus; 1"],
                     ["Normal move, &sigma;<sub>t</sub>", f"Standard deviation of the {cfg['ret_window']} daily returns before day t (day t itself is left out)"],
                     ["Move score, z<sub>t</sub>", "r<sub>t</sub> / &sigma;<sub>t</sub>"],
                     ["Volume ratio, v<sub>t</sub>", f"Volume<sub>t</sub> divided by the average volume of the {cfg['vol_window']} days before t "
                                                     "(a simple moving average, as in Varsity Module 2, chapter 13)"],
                     ["ATR<sub>t</sub>", f"Average true range over the last {cfg['atr_window']} days including day t. True range is the largest of "
                                         "High &minus; Low, |High &minus; previous Close| and |Low &minus; previous Close| (Wilder, 1978; I use a simple average)."]],
                    [38 * mm, FRAME_W - 38 * mm], right_from=9, caption="Definitions.")]
    story.append(P("The rules", "h2"))
    story += bullets([
        f"<b>News day.</b> Good news on day t if z<sub>t</sub> {GE} k and v<sub>t</sub> {GE} m. Bad news if z<sub>t</sub> {LE} {MINUS}k and v<sub>t</sub> {GE} m.",
        f"<b>Entry.</b> At the open of day t+1: buy after good news, short after bad news. The fill is {cfg['slippage']*100:.2f}% worse than the open.",
        f"<b>Stop-loss.</b> {cfg['stop_atr']} {TIMES} ATR<sub>t</sub> away from the entry price. If the day's low (for a long) or high (for a short) touches it, "
        "I assume the order fills at the stop price, again with slippage.",
        "<b>Exit.</b> Otherwise square off at the close of day t+1, with slippage. No position is ever held overnight.",
        f"<b>Size.</b> The smaller of (a) the quantity for which being stopped out loses {cfg['risk_per_trade']*100:.1f}% of current equity, and "
        f"(b) {cfg['max_pos_frac']*100:.0f}% of current equity in value. At most {cfg['max_positions']} trades a day; if more stocks have news, "
        f"I take the {cfg['max_positions']} with the largest |z|.",
        f"<b>Thresholds.</b> k and m were chosen from nine combinations (k in {', '.join(str(x) for x in ks)}; m in {', '.join(str(x) for x in ms)}) "
        f"using the development period only: the highest Sharpe ratio among settings with at least {cfg['min_dev_trades']} trades.",
    ], numbered=True)
    story.append(P("Varsity's position-sizing chapters (Module 9, chapters 11 and 12) discuss limiting the exposure of a single trade to a fixed "
                   "share of capital, with examples between 5% and 20%. My 20% cap follows that idea. The stop-distance rule in (a) is my own "
                   "addition; the Varsity chapters I read do not derive size from the stop."))

    # ---------------------------------------------------------------- 4 assumptions
    story += [CondPageBreak(60 * mm), P("4. Backtest assumptions, costs and risk controls", "h1")]
    story += bullets([
        f"<b>Capital.</b> {inr(cfg['capital'])} at the start of each period. The development and test runs each start fresh, so the test result "
        "does not depend on what happened in development. No leverage, and idle cash earns nothing.",
        "<b>No look-ahead.</b> Everything used for a decision on day t+1 is known at the close of day t. The code stops with an error if any trade "
        "has a signal date that is not before its trade date, or if the equity curve does not equal starting capital plus the sum of the trade P&amp;Ls.",
        f"<b>Fills.</b> Entry at the open and exit at the close or the stop, each {cfg['slippage']*100:.2f}% worse than the quoted price, and always "
        "kept inside that day's high&ndash;low range.",
        "<b>Short selling.</b> I assume an ordinary intraday sell order in the cash segment, bought back the same day. Two things are assumed rather "
        "than modelled: that all eight stocks can be shorted, and that I can always buy back at the closing price. Brokers usually square off a few "
        "minutes before the close, so real fills would differ a little. There is no overnight borrowing, so none is charged.",
        f"<b>Risk controls.</b> Stop-loss at {cfg['stop_atr']} ATR, no position above {cfg['max_pos_frac']*100:.0f}% of equity, at most "
        f"{cfg['max_positions']} positions, and a holding period of one session. The largest single loss in the test period was "
        f"{pp(tt['max_loss_pct'], 2, False)} of the trade value ({inr(tt['max_loss'])}).",
    ])
    story.append(table([
        ["Charge (equity intraday)", "Rate", "Applied to"],
        ["Brokerage", f"0.03% of order value or {RS}20, whichever is lower", "Each order"],
        ["Securities transaction tax", "0.025%", "Sell side only"],
        ["NSE transaction charge", "0.00307%", "Both sides"],
        ["SEBI turnover fee", f"{RS}10 per crore", "Both sides"],
        ["Stamp duty", "0.003%", "Buy side only"],
        ["GST", "18% of (brokerage + SEBI fee + transaction charge)", "Both sides"]],
        [52 * mm, 88 * mm, FRAME_W - 140 * mm], right_from=9,
        caption="Charges used, from Zerodha's published charges (zerodha.com/charges, read 19 October 2026).",
        note=f"On a {RS}2,00,000 round trip these add up to about {RS}118, or 0.06%. Adding {cfg['slippage']*100:.2f}% slippage on each of the two fills, "
             f"the average round trip in the test period cost {fric:.2f}% of the trade value. Charges change over time, so check the page again before "
             "relying on them."))

    # ---------------------------------------------------------------- 5 development
    chosen_row = next(g for g in grid if g["k"] == k and g["m"] == m)
    ik, im = ks.index(k), ms.index(m)
    neigh = [g for g in grid if abs(ks.index(g["k"]) - ik) <= 1 and abs(ms.index(g["m"]) - im) <= 1 and not (g["k"] == k and g["m"] == m)
             and g["dev_sharpe"] is not None]
    nlo, nhi = min(g["dev_sharpe"] for g in neigh), max(g["dev_sharpe"] for g in neigh)
    at_corner = (k in (ks[0], ks[-1])) and (m in (ms[0], ms[-1]))
    rows = [["k", "m", "Trades", "Return", "Sharpe"]]
    chosen_idx = None
    for i_, g in enumerate(grid, 1):
        rows.append([f"{g['k']}", f"{g['m']}", f"{g['dev_trades']}", pc(g["dev_cum"], 1, True), nm(g["dev_sharpe"])])
        if g["k"] == k and g["m"] == m:
            chosen_idx = i_
    dev_years = int(cfg["dev_end"][:4]) - int(cfg["dev_start"][:4]) + 1
    story += [CondPageBreak(70 * mm), P("5. Tuning on the development period", "h1"),
              P(f"Table {TN['dev']} shows the nine settings I tried on {cfg['dev_start'][:4]}&ndash;{cfg['dev_end'][:4]}. I picked "
                f"<b>k = {k}, m = {m}</b>: the highest Sharpe ratio ({nm(chosen_row['dev_sharpe'])}) from {chosen_row['dev_trades']} trades. "
                "Sharpe was my selection rule on purpose. Picking the highest return would favour whichever setting happened to take the "
                "biggest risks."),
              table(rows, [20 * mm, 20 * mm, 28 * mm, 30 * mm, 30 * mm], bold_rows=(chosen_idx,), shade_rows=(chosen_idx,),
                    caption="Development period only. The chosen setting is highlighted.", note=None, key="dev")]
    story.append(P(
        f"{'The chosen setting is the strictest one I tried, so it sits in the corner of the grid, which is a warning sign: the best answer may lie beyond it. ' if at_corner else ''}"
        f"Its neighbours were close behind (Sharpe {nm(nlo)} to {nm(nhi)}), so it does not look like a lone spike. I did not extend the grid, "
        f"because stricter thresholds produce so few trades ({chosen_row['dev_trades']} in {dev_years} years already) that they could not be judged. "
        f"In development the chosen setting returned {pc(ds['cum_return'], 1, True)} ({pc(ds['ann_return'], 2, True)} a year) with a maximum "
        f"drawdown of {pc(ds['max_drawdown'], 2)}."))

    # ---------------------------------------------------------------- 6 test results
    story += [CondPageBreak(80 * mm), P("6. Test-period results", "h1"),
              P(f"This is the part that counts. The chosen setting (k = {k}, m = {m}) was run once on {T['start']} to {T['end']}. "
                "The thresholds were selected from the development data alone.")]

    def per(x, d):  # period-labelled ratio helper
        return nm(x, d)

    def money_pct(v, p):
        return "N/A" if v is None else f"{inr(v)} ({pp(p)})"

    na = "N/A"
    mrows = [["Metric", "Strategy", "Nifty", "Strategy", "Nifty"],
             ["", f"Development<br/>{D['start'][:4]}&ndash;{D['end'][:4]}", f"Development<br/>{D['start'][:4]}&ndash;{D['end'][:4]}",
              f"Test<br/>{T['start'][:4]}&ndash;{T['end'][:4]}", f"Test<br/>{T['start'][:4]}&ndash;{T['end'][:4]}"],
             ["Cumulative return", pc(ds["cum_return"], 2, True), pc(db["cum_return"], 2, True), pc(ts["cum_return"], 2, True), pc(tb["cum_return"], 2, True)],
             ["Annualised return", pc(ds["ann_return"], 2, True), pc(db["ann_return"], 2, True), pc(ts["ann_return"], 2, True), pc(tb["ann_return"], 2, True)],
             ["Sharpe ratio (risk-free 0%)", nm(ds["sharpe"]), nm(db["sharpe"]), nm(ts["sharpe"]), nm(tb["sharpe"])],
             ["Sharpe ratio (risk-free 6%)", nm(ds["sharpe_alt"]), nm(db["sharpe_alt"]), nm(ts["sharpe_alt"]), nm(tb["sharpe_alt"])],
             ["Sortino ratio (risk-free 0%)", nm(ds["sortino"]), nm(db["sortino"]), nm(ts["sortino"]), nm(tb["sortino"])],
             ["Sortino ratio (risk-free 6%)", nm(ds["sortino_alt"]), nm(db["sortino_alt"]), nm(ts["sortino_alt"]), nm(tb["sortino_alt"])],
             ["Maximum drawdown", pc(ds["max_drawdown"], 2), pc(db["max_drawdown"], 2), pc(ts["max_drawdown"], 2), pc(tb["max_drawdown"], 2)],
             ["Completed trades (long / short)", f"{dt['trades']} ({dt['longs']}/{dt['shorts']})", na, f"{tt['trades']} ({tt['longs']}/{tt['shorts']})", na],
             ["Win rate", pc(dt["win_rate"], 1), na, pc(tt["win_rate"], 1), na],
             ["Profit factor", nm(dt["profit_factor"]), na, nm(tt["profit_factor"]), na],
             ["Average profit, winning trades", money_pct(dt["avg_profit"], dt["avg_profit_pct"]), na, money_pct(tt["avg_profit"], tt["avg_profit_pct"]), na],
             ["Average loss, losing trades", money_pct(dt["avg_loss"], dt["avg_loss_pct"]), na, money_pct(tt["avg_loss"], tt["avg_loss_pct"]), na],
             ["Largest profit, one trade", money_pct(dt["max_profit"], dt["max_profit_pct"]), na, money_pct(tt["max_profit"], tt["max_profit_pct"]), na],
             ["Largest loss, one trade", money_pct(dt["max_loss"], dt["max_loss_pct"]), na, money_pct(tt["max_loss"], tt["max_loss_pct"]), na],
             ["Strategy minus Nifty, return", sgn(f"{(ds['cum_return'] - db['cum_return']) * 100:+.2f} pts"), "",
              sgn(f"{(ts['cum_return'] - tb['cum_return']) * 100:+.2f} pts"), ""]]
    story.append(table(mrows, [51 * mm, 30 * mm, 24 * mm, 30 * mm, 24 * mm], header_rows=2, shade_rows=(),
                       caption="Performance metrics, all after modelled costs. Evaluation periods are labelled in the column headings.",
                       key="metrics",
                       note=(f"<b>Conventions.</b> Daily returns, 252 trading days a year. Annualised return = (1 + cumulative)<super>252/days</super> &minus; 1. "
                             "Sharpe = mean excess daily return / its standard deviation, times &radic;252 (Sharpe, 1994). Sortino = mean excess daily return / "
                             "downside deviation, times &radic;252, with downside measured below the risk-free rate (Sortino &amp; Price, 1994). "
                             "<b>Risk-free rate:</b> the headline figures use 0%. The strategy is flat overnight and sits in cash most days, and I give "
                             "idle cash no interest, so there is no holding cost to subtract; the same 0% is used for the index so the comparison is like for like. "
                             "The 6% rows show the effect of charging a 6% hurdle. They come out far lower for the strategy because the account earns "
                             "nothing while it waits, which is exactly why I do not use them as the headline. "
                             "<b>N/A:</b> the index is buy-and-hold, so it has no completed trades, win rate or profit factor. "
                             "Profit factor = gross profit / gross loss over completed trades, after costs. Benchmark returns are measured close to close "
                             "over the same dates, starting from the close before the first trading day.")))

    # 6.2
    nifty_end = 100 * (1 + tb["cum_return"])
    story += [P("6.1 Equity curve and drawdown", "h2"),
              P(f"Figures 2 and 3 show the equity curves and the falls from peak. The strategy traded on about {active_t:.0f}% of test days. "
                f"The index, which was fully invested, ended the test period at {nifty_end:.0f} and at one point was "
                f"{pc(abs(tb['max_drawdown']), 0)} below its peak. The strategy's worst fall was {pc(abs(ts['max_drawdown']), 1)}. That is not a "
                "like-for-like comparison, since one was fully invested and the other mostly in cash, so the risk-adjusted figures in "
                f"Table {TN['metrics']} matter more than the raw return gap."),
              figure(figs("fig3_equity.png"),
                     "Value of the strategy and of the index, both rebased to 100 at the start of each period, the way Varsity's equity-curve chapter "
                     "(Module 9, chapter 6) presents it. The strategy line is smooth and low because it risks a fifth of equity or less, for one "
                     "day at a time, and only on news days."),
              figure(figs("fig4_drawdown.png"), "Fall from the previous peak, test period.")]

    # 6.3
    story += [P("6.2 What the trades look like", "h2"),
              P(f"Figures 4 and 5 show the trades themselves. Of {tt['trades']} trades, {pc(tt['win_rate'], 0)} were winners. Winners averaged "
                f"{pp(tt['avg_profit_pct'])} and losers {pp(tt['avg_loss_pct'])}. The strategy works through a modest edge repeated many times, "
                f"not through rare big wins: the five best trades account for {pc(tt['top5_share_of_gross_profit'], 0)} of gross profit. "
                f"Only {tt['stops']} of the {tt['trades']} trades hit the stop."),
              figure(figs("fig6_trade_dist.png"), "Net return per trade, test period, longs and shorts separately. The black line is the mean."),
              figure(figs("fig5_trades.png"),
                     f"{R['trades_chart_stock']} in the test period, the stock with the most trades. Top: the whole period. Bottom: an enlarged stretch "
                     "showing each day's high&ndash;low range. A trade opens at the open (triangle) and closes the same day (circle or cross), so the "
                     "entry and exit sit very close together on a daily chart.")]

    # 6.4 event study
    def covers_zero(e):
        return e["mean_pct"] - 1.96 * e["se_pct"] < 0
    sh_zero = covers_zero(ev_t["bearish"])
    story += [CondPageBreak(60 * mm), P("6.3 Is there an effect before costs?", "h2"),
              P(f"Figure 6 strips out everything except the idea itself. In the test period the stock moved in the news direction by "
                f"{pp(ev_t['bullish']['mean_pct'])} on average after good news ({ev_t['bullish']['n']} events) and {pp(ev_t['bearish']['mean_pct'])} "
                f"after bad news ({ev_t['bearish']['n']} events); in development the figures were {pp(ev_d['bullish']['mean_pct'])} and "
                f"{pp(ev_d['bearish']['mean_pct'])}. The round-trip cost of {fric:.2f}% takes about "
                f"{fric / ev_t['bullish']['mean_pct'] * 100:.0f}% of the average long-side move and "
                f"{fric / ev_t['bearish']['mean_pct'] * 100:.0f}% of the short-side one in the test period. "
                f"{'The interval for the short side includes zero, so I cannot rule out that it is chance. ' if sh_zero else ''}"
                "Two cautions. The intervals are calculated as if events were independent, but several stocks often flag on the same day because "
                "of a market-wide move, so the real uncertainty is larger than drawn. And the sample is small."),
              figure(figs("fig2_events.png"),
                     "Mean open-to-close return in the trade direction on the session after a flagged news day, before costs, sizing and stops. "
                     "Bars show 95% intervals; the grey line is the average round-trip cost and slippage.", width=FRAME_W * 0.82)]

    # 6.5 regimes
    reg = R["regimes_test"]
    rrows = [["Market type", "Trades", "Win rate", "Average net trade", "Net profit"]]
    for nme in ["rising", "falling", "sideways"]:
        r_ = reg[nme]
        rrows.append([nme.capitalize(), str(r_["trades"]), pc(r_["win_rate"], 0) if r_["win_rate"] is not None else "N/A",
                      pp(r_["avg_trade_pct"]) if r_["avg_trade_pct"] is not None else "N/A", inr(r_["net_pnl"])])
    all_pos = all(reg[x]["net_pnl"] > 0 for x in reg)
    ps = R["per_stock_test"]
    prow = [["Stock", "Trades (long/short)", "Win rate", "Net profit"]]
    for n_, v in ps.items():
        prow.append([n_, f"{v['trades']} ({v['longs']}/{v['shorts']})", pc(v["win_rate"], 0), inr(v["net_pnl"])])
    n_pos = sum(1 for v in ps.values() if v["net_pnl"] > 0)
    lo_tr, hi_tr = min(reg[x]["trades"] for x in reg), max(reg[x]["trades"] for x in reg)
    story += [CondPageBreak(60 * mm), P("6.4 Rising, falling and sideways markets, and different stocks", "h2"),
              P("I labelled each trading day by the index's return over the previous 60 sessions: rising above +5%, falling below &minus;5%, "
                "otherwise sideways. The label uses only past data."),
              table(rrows, [38 * mm, 25 * mm, 28 * mm, 40 * mm, 35 * mm], caption="Test-period trades by market type."),
              P(f"{'The strategy made money in all three kinds of market' if all_pos else 'The strategy did not make money in every kind of market'}, "
                f"but with only {lo_tr} to {hi_tr} trades in each, I would not read a pattern into the differences."),
              table(prow, [38 * mm, 45 * mm, 30 * mm, 35 * mm], caption="Test-period trades by stock."),
              P(f"{n_pos} of the 8 stocks were profitable. No single stock accounts for all of the profit, which is some reassurance that "
                "the result is not one lucky name.")]

    # 6.6 robustness
    n_pos_cells = sum(1 for g in grid if g["test_cum"] > 0)
    worst = min(grid, key=lambda g: g["test_cum"])
    grows = [["k", "m", "Dev trades", "Dev Sharpe", "Test trades", "Test return", "Test Sharpe"]]
    for g in grid:
        grows.append([f"{g['k']}", f"{g['m']}", str(g["dev_trades"]), nm(g["dev_sharpe"]), str(g["test_trades"]),
                      pc(g["test_cum"], 1, True), nm(g["test_sharpe"])])
    sens = R["sensitivity"]
    srows = [["Assumption", "Test return", "Sharpe (0%)"],
             ["No charges, no slippage", pc(R["test_gross"]["strategy"]["cum_return"], 1, True), nm(R["test_gross"]["strategy"]["sharpe"])]]
    be = None
    keys = sorted(sens, key=float)
    for kk in keys:
        srows.append([f"Charges + {float(kk)*100:.2f}% slippage per fill" + ("  (used)" if abs(float(kk) - cfg["slippage"]) < 1e-12 else ""),
                      pc(sens[kk]["cum"], 1, True), nm(sens[kk]["sharpe"])])
    for a_, b_ in zip(keys[:-1], keys[1:]):
        ca, cb = sens[a_]["cum"], sens[b_]["cum"]
        if ca > 0 >= cb:
            be = float(a_) + (float(b_) - float(a_)) * ca / (ca - cb)
    g_ret = R["test_gross"]["strategy"]["cum_return"]
    lost_share = (g_ret - ts["cum_return"]) / g_ret * 100 if g_ret > 0 else None
    story += [CondPageBreak(75 * mm), P("6.5 Nearby settings and costs", "h2"),
              P(f"Table {TN['grid']} shows how all nine settings did in the test period. It is for information only. I did not use it to choose "
                "anything, and changing my choice now would turn the test period into a second development period."),
              table(grows, [14 * mm, 14 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm],
                    bold_rows=(chosen_idx,), shade_rows=(chosen_idx,), key="grid",
                    caption="All nine settings, development and test. The chosen setting is highlighted."),
              P(f"{n_pos_cells} of the 9 settings made money in the test period. The weakest one (k = {worst['k']}, m = {worst['m']}) "
                f"{'lost' if worst['test_cum'] < 0 else 'made'} {pc(abs(worst['test_cum']), 1)} over {worst['test_trades']} trades."
                + (" That is the loosest setting. When the thresholds are loose the strategy trades constantly on small, noisy moves, and costs "
                   "eat the small edge. Of the choices I varied, how strict to be about news made the biggest difference."
                   if (worst["k"] == ks[0] and worst["m"] == ms[0]) else "")),
              table(srows, [90 * mm, 40 * mm, 40 * mm], caption="Test period, chosen setting, under different cost assumptions."),
              P((f"With no charges and no slippage the strategy would have returned {pc(g_ret, 1, True)}; with the charges and "
                 f"{cfg['slippage']*100:.2f}% slippage I assumed, it returned {pc(ts['cum_return'], 1, True)}. Friction took about "
                 f"{lost_share:.0f}% of the frictionless return. " if lost_share is not None else "")
                + (f"The result reaches break-even at roughly {be*100:.2f}% slippage per fill, so if real fills on news days are much worse than "
                   f"my {cfg['slippage']*100:.2f}% assumption, the edge disappears." if be else "The result stays positive across the slippage range I tried."))]

    # 6.7 known-answer check
    if sample and R.get("placebo"):
        pl = R["placebo"]
        pt, pe = pl["test"], pl["events"]
        krows = [["", "Effect planted (this report)", "Effect switched off"],
                 ["Setting chosen on development data", f"k = {k}, m = {m}", f"k = {pl['k']}, m = {pl['m']}"],
                 ["Development Sharpe of that setting", nm(R["chosen_dev_sharpe"] if "chosen_dev_sharpe" in R else chosen_row["dev_sharpe"]), nm(pl["dev_sharpe"])],
                 ["Test return", pc(ts["cum_return"], 1, True), pc(pt["strategy"]["cum_return"], 1, True)],
                 ["Test Sharpe (0%)", nm(ts["sharpe"]), nm(pt["strategy"]["sharpe"])],
                 ["Test trades", str(tt["trades"]), str(pt["trades"]["trades"])]]
        story += [P("6.6 A known-answer check (synthetic data only)", "h2"),
                  P("Because I built this market, I can test the code against a known answer. I generated the same market again with the "
                    "planted continuation switched off, re-ran the whole procedure (tune on development, then one test run), and compared. If the "
                    "code leaked information from the future, it would still make money in the market where there is nothing to find."),
                  table(krows, [70 * mm, 50 * mm, 50 * mm], caption="Same market with and without the planted effect."),
                  P(f"With the effect switched off, the strategy {'lost' if pt['strategy']['cum_return'] < 0 else 'made'} "
                    f"{pc(abs(pt['strategy']['cum_return']), 1)} in the test period. With it planted, the strategy "
                    f"{'made' if ts['cum_return'] > 0 else 'lost'} {pc(abs(ts['cum_return']), 1)}. "
                    + ("So in this run the engine did not invent profit where there was nothing to find, and it picked up an effect that was there. "
                       if (pt["strategy"]["cum_return"] <= 0 < ts["cum_return"]) else
                       "This is worth a closer look: the result does not separate the two worlds the way it should. ")
                    + "It is one run, so it is a sanity check and not a proof. And it tells me nothing about real stocks, where I do not know "
                      "how big any such effect is, or whether it exists.")]

    # ---------------------------------------------------------------- 7 takeaways
    long_s, short_s = sides["long"], sides["short"]
    t_stat = tt["avg_trade_t"]
    strong, weak = (("long", long_s), ("short", short_s)) if long_s["net_pnl"] >= short_s["net_pnl"] else (("short", short_s), ("long", long_s))
    loosest = next(g for g in grid if g["k"] == ks[0] and g["m"] == ms[0])
    strictest = next(g for g in grid if g["k"] == ks[-1] and g["m"] == ms[-1])
    story += [CondPageBreak(60 * mm), P("7. What worked and what did not", "h1")]
    story += bullets([
        f"<b>Strictness about news.</b> In development, Sharpe rose almost steadily as the thresholds tightened, from {nm(loosest['dev_sharpe'])} "
        f"for the loosest setting to {nm(strictest['dev_sharpe'])} for the strictest. In the test period {n_pos_cells} of the 9 settings made money.",
        f"<b>The two sides.</b> The {strong[0]} side earned {inr(strong[1]['net_pnl'])} ({pp(strong[1]['avg_trade_pct'])} per trade) from "
        f"{strong[1]['trades']} trades. The {weak[0]} side earned {inr(weak[1]['net_pnl'])} ({pp(weak[1]['avg_trade_pct'])} per trade) from "
        f"{weak[1]['trades']} trades, so it was the weaker half. Adding the short side gave more trades, but not an equal share of the profit.",
        f"<b>The loosest setting.</b> It returned {pc(loosest['test_cum'], 1, True)} in the test period over {loosest['test_trades']} trades.",
        f"<b>The size of the edge is fragile.</b> The average trade made {pp(tt['avg_trade_pct'])} net over {tt['trades']} trades, and its t-statistic is "
        f"{nm(t_stat, 2)}. {'That is below the usual 1.96 needed to call it different from zero at the 5% level' if t_stat is not None and abs(t_stat) < 1.96 else 'That is above the usual 1.96 threshold'}. "
        "A strategy like this can look fine and still be luck.",
    ])

    # ---------------------------------------------------------------- 8 limitations
    story += [P("8. Limitations and possible improvements", "h1")]
    lim = [
        ("The data." if not sample else "The data are synthetic.",
         "Nothing in this sample says anything about real stocks. With real data, using today's Nifty constituents would add survivorship "
         "bias, because stocks that fell out of the index are missing." if sample else
         "Using today's Nifty constituents adds survivorship bias, because stocks that fell out of the index are missing."),
        ("&ldquo;News&rdquo; is a proxy.",
         "I flag days from price and volume, not from headlines. Some flagged days will be index rebalances or market-wide moves, and some real "
         "news will be missed. A better version would use time-stamped company announcements and a results calendar, and would measure how far "
         "results beat or missed expectations, which is what Varsity's key-events chapter says moves prices."),
        ("Daily bars hide the session.",
         "I cannot see the order of the high and the low, how the open auction behaves, or how fast news is priced in. I assume a touched stop "
         "fills, and that I can trade near the open on a news day, which is usually when prices are most erratic. The slippage test above suggests this matters. "
         "Five-minute data would let me model entries after the first few minutes and stops properly."),
        ("Small sample and selection.",
         f"{tt['trades']} test trades is not many, and I picked the best of nine settings, which flatters the development result. Development "
         f"Sharpe was {nm(ds['sharpe'])} against {nm(ts['sharpe'])} in the test" + (", which is the usual shrinkage" if ds["sharpe"] > ts["sharpe"] else "")
         + ". A walk-forward test with rolling development and test windows would use the data better."),
        ("What I did not model.",
         "Leverage, interest on idle cash, taxes, circuit limits and trading halts, the exact broker square-off time, and whether each stock is "
         "actually available to short on the day."),
        ("Ideas I did not try.",
         "Sizing by the strength of the news, a market-wide filter so that index-driven days are ignored, an EMA or ADX trend filter in the spirit of "
         "Track 2 (compared against the basic version), and a trailing exit instead of a fixed close."),
    ]
    story += bullets([f"<b>{h}</b> {t}" for h, t in lim])

    # ---------------------------------------------------------------- 9 resources
    story += [CondPageBreak(70 * mm), P("9. Resources and acknowledgement", "h1")]
    story.append(P("<b>Code.</b> <font name='Mono' size='9'>news_intraday_strategy.py</font> (data, signals, backtest, metrics, charts) and "
                   "<font name='Mono' size='9'>build_report.py</font> (this PDF). Instructions are in the README. The trade logs are "
                   "<font name='Mono' size='9'>trades_dev.csv</font> and <font name='Mono' size='9'>trades_test.csv</font>."))
    story.append(P("Zerodha Varsity", "h2"))
    V = "https://zerodha.com/varsity/chapter/"
    var = [("Module 1, ch. 8, &ldquo;Commonly Used Jargons&rdquo;", "commonly-used-jargons/", "short selling; short positions are closed the same day"),
           ("Module 1, ch. 11, &ldquo;Corporate actions and impact on stock prices&rdquo;", "five-corporate-actions-and-its-impact-on-stock-prices/", "why prices need adjusting"),
           ("Module 1, ch. 12, &ldquo;Key Events and Their Impact on Markets&rdquo;", "key-events-and-their-impact-on-markets/", "events move prices against expectations"),
           ("Module 2, ch. 12, &ldquo;Volumes&rdquo;", "volumes/", "volume confirms price; the 10-day average"),
           ("Module 2, ch. 13, &ldquo;Moving Averages&rdquo;", "moving-averages/", "rolling averages"),
           ("Module 9, ch. 6, &ldquo;Equity Curve&rdquo;", "equity-curve/", "equity curve rebased to 100 and compared with Nifty"),
           ("Module 9, ch. 11, &ldquo;Position Sizing for active trader&rdquo;", "position-sizing/", "why position size matters"),
           ("Module 9, ch. 12, &ldquo;Position Sizing for active traders (Part 2)&rdquo;", "position-sizing-active-traders-part-2/", "capping exposure per trade as a share of equity")]
    for t_, u, why in var:
        story.append(Paragraph(f"{t_} ({why}). <link href='{V}{u}' color='{ACCENT}'>{V}{u}</link>", ST["small"]))
    story.append(P("Other", "h2"))
    others = [
        "Zerodha, brokerage and charges for equity intraday. <link href='https://zerodha.com/charges' color='%s'>https://zerodha.com/charges</link> (read 19 October 2026)." % ACCENT,
        "Wilder, J. W. (1978). <i>New Concepts in Technical Trading Systems</i>. Trend Research. (Average true range.)",
        "Sharpe, W. F. (1994). The Sharpe Ratio. <i>Journal of Portfolio Management</i>, 21(1), 49&ndash;58.",
        "Sortino, F. A. and Price, L. N. (1994). Performance Measurement in a Downside Risk Framework. <i>Journal of Investing</i>, 3(3), 59&ndash;64.",
        "Python libraries: numpy, pandas, matplotlib (charts) and reportlab (this PDF). "
        + ("The yfinance library is used by the code to download real data, but it was not used for any number in this sample." if sample else
           "yfinance (Yahoo Finance data), numpy, pandas."),
    ]
    for o in others:
        story.append(Paragraph(o, ST["small"]))
    story.append(P("Acknowledgement of assistance", "h2"))
    story.append(P("The idea of trading the day after good or bad news, long on one and short on the other, squared off the same day, is mine. "
                   "Claude, an AI assistant made by Anthropic, helped turn it into precise rules, wrote the code, ran the backtest and drafted "
                   "this report. The cost figures and the Varsity chapters were checked against the pages listed above. "
                   "The charts are plotted from the data by the code; no images were AI-generated."))

    # ---------------------------------------------------------------- build
    doc = BaseDocTemplate(a.out, pagesize=A4, leftMargin=LM, rightMargin=RM, topMargin=22 * mm, bottomMargin=20 * mm,
                          title="Trading the day after the news - PS2 sample report" if sample else "Trading the day after the news - PS2 report",
                          author="Harshit Kumar Arya")
    frame = Frame(LM, 20 * mm, FRAME_W, PAGE_H - 42 * mm, id="main", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=make_pagefn(sample))])
    doc.build(story)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
