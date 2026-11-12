import os
import re
import io
import csv
import json
import time
import smtplib
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from collections import Counter, defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import requests
import feedparser
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openai import OpenAI
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
                                CondPageBreak, KeepTogether, HRFlowable)

import report_html

try:
    import yfinance as yf
except ImportError:
    yf = None

# ------------------------------------------------------------------ CONFIG
TZ = ZoneInfo("Africa/Nairobi")
NOW = datetime.now(TZ)
TODAY = NOW.strftime("%Y-%m-%d")
TODAY_PRETTY = NOW.strftime("%A, %d %B %Y")

RECIPIENT_EMAIL = "steveotieno2012005@gmail.com"
SENDER_EMAIL = "steveotieno2012005@gmail.com"
GITHUB_USER = "Pinyotimo"
GH_PAT = os.environ.get("GH_PAT")  # PAT (read:user, repo) -> includes private activity

LOCATION = {"name": "Nairobi", "lat": -1.2921, "lon": 36.8219}

# NSE tickers. Dangote Petroleum Refinery is NOT listed on any exchange, so it cannot be priced.
# Quickmart (QMART) is not in the NSE listing either; it shows "not found" and will appear
# automatically if it ever lists. Eveready East Africa is "EVRD"; EGAD is Eaagads.
NSE_WATCHLIST = {
    "Co-op Bank": "COOP",
    "Stanbic Holdings": "SBIC",
    "EABL": "EABL",
    "BK Group": "BKG",
    "Eaagads": "EGAD",
    "Quickmart": "QMART",
}
HISTORY_START = "2026-11-10"  # ignore stored prices before this (older scraper was inaccurate)

GLOBAL_TECH = {"NVIDIA": "NVDA", "Microsoft": "MSFT", "Apple": "AAPL",
               "Alphabet": "GOOGL", "Amazon": "AMZN", "Meta": "META"}

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
HISTORY_CSV = DATA_DIR / "nse_history.csv"
REDDIT_CACHE = DATA_DIR / "reddit_cache.json"
ALERT_STATE = DATA_DIR / "alert_state.json"
OUT_DIR = Path("output")
OUT_DIR.mkdir(exist_ok=True)

RUN_MODE = os.environ.get("RUN_MODE", "digest").lower()
ALERT_MOVE_THRESHOLD = float(os.environ.get("ALERT_MOVE_THRESHOLD", "3"))
ALERT_KEYWORDS = tuple(
    keyword.strip().lower()
    for keyword in os.environ.get("ALERT_KEYWORDS", "AI,cybersecurity,Kenya startup").split(",")
    if keyword.strip()
)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

NEWS_FEEDS = {
    "Cybersecurity": [
        "https://feeds.feedburner.com/TheHackersNews",
        "https://www.bleepingcomputer.com/feed/",
    ],
    "DevOps & Cloud": [
        "https://www.cncf.io/feed/",
        "https://devops.com/feed/",
    ],
    "Fintech": [
        "https://techcabal.com/category/fintech/feed/",
        "https://www.finextra.com/rss/headlines.aspx",
    ],
    "Tech Giants": [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
    ],
    "Kenya Tech & Events": [
        "https://news.google.com/rss/search?q=Nairobi+tech+event+OR+hackathon+OR+summit+Kenya&hl=en-KE&gl=KE&ceid=KE:en",
        "https://techweez.com/feed/",
    ],
}
REDDIT_LANGS = {"Rust": "rust", "C#": "csharp", "Java": "java",
                "Node.js": "node", "Kenya Tech": "KenyaTech"}

# Luma's public "discover" feed for Nairobi (id taken from https://lu.ma/nairobi).
LUMA_PLACE_ID = "discplace-YSx1DPerjjIyq7M"
# Funds shown in the MMF section. No yields are shown until a yield collector exists.
MMF_WATCH = ["Arvocap Money Market Fund", "Mansa-X / Mansa Plus", "OAK Money Market Fund"]

# ------------------------------------------------------------------ LLM
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if GROQ_API_KEY:
    client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)
    MODEL_NAME = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
    LLM_NAME = f"Groq ({MODEL_NAME})"
else:
    client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
    MODEL_NAME, LLM_NAME = "llama3.2", "Ollama Llama 3.2 (local)"


def llm(system: str, user: str, max_tokens: int = 350) -> str:
    try:
        r = client.chat.completions.create(
            model=MODEL_NAME, max_tokens=max_tokens, temperature=0.3,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"  ! LLM failed: {e}")
        return ""


def safe(fn, default, label=""):
    try:
        return fn()
    except Exception as e:
        print(f"  ! {label or fn.__name__} failed: {e}")
        return default


# ------------------------------------------------------------------ FONTS
# DejaVu TTFs ship inside matplotlib, so nothing to download (works on GitHub Actions).
FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
try:
    for _n in ("DejaVuSans", "DejaVuSans-Bold", "DejaVuSans-Oblique", "DejaVuSans-BoldOblique",
               "DejaVuSerif", "DejaVuSerif-Bold", "DejaVuSansMono", "DejaVuSansMono-Bold"):
        pdfmetrics.registerFont(TTFont(_n, str(FONT_DIR / f"{_n}.ttf")))
    pdfmetrics.registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
                                  italic="DejaVuSans-Oblique", boldItalic="DejaVuSans-BoldOblique")
    SANS, SANS_B, SERIF_B, MONO, MONO_B = ("DejaVuSans", "DejaVuSans-Bold", "DejaVuSerif-Bold",
                                           "DejaVuSansMono", "DejaVuSansMono-Bold")
except Exception as _e:
    print(f"  ! font registration failed, using built-ins: {_e}")
    SANS, SANS_B, SERIF_B, MONO, MONO_B = "Helvetica", "Helvetica-Bold", "Times-Bold", "Courier", "Courier-Bold"


# ------------------------------------------------------------------ THEME / CHART HELPERS
# Same design tokens as the HTML dashboard (report_html.py).
NAVY, MIDNIGHT, BLUE, GREEN, RED, GREY, LIGHT = ("#101827", "#172554", "#3B82F6", "#16A34A", "#EF4444",
                                                 "#64748B", "#F1F5F9")
PURPLE, TEAL, AMBER = "#8B5CF6", "#14B8A6", "#F59E0B"
PALETTE = ["#2563EB", "#0D9488", "#7C3AED", "#D97706", "#DB2777"]

plt.rcParams.update({
    "font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#CBD5E1", "axes.labelcolor": GREY, "xtick.color": GREY,
    "ytick.color": GREY, "axes.grid": True, "grid.color": "#E2E8F0", "grid.linewidth": 0.6,
})


def ttl(ax, text, **kw):
    ax.set_title(text, loc="left", fontfamily="DejaVu Serif", fontweight="bold", color=NAVY, **kw)


def fig_to_image(fig, width_mm=175) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    iw, ih = ImageReader(buf).getSize()  # real pixel size (bbox_inches="tight" changes it)
    buf.seek(0)
    return Image(buf, width=width_mm * mm, height=width_mm * mm * (ih / iw))


def latin(s: str) -> str:
    """Name kept for compatibility. DejaVu is Unicode, so only strip emoji/zero-width chars."""
    return "".join(c for c in (s or "") if ord(c) < 0x10000 and not 0xFE00 <= ord(c) <= 0xFE0F
                   and not 0x200B <= ord(c) <= 0x200F)


def P(text, style):  # safe paragraph
    return Paragraph(escape(latin(text)), style)


def link_markup(title, url):
    return f'<a href="{escape(url)}" color="{BLUE}">{escape(latin(title))}</a>'


def sparkline_grid(cards, title, cols=3):
    """Card grid: label, big value, colored change texts, mini trend line.
    card = {label, value, subs:[(text, up|None), ...], ys:[...], up: bool|None}"""
    if not cards:
        return None
    rows = (len(cards) + cols - 1) // cols
    head = 0.55
    fig, axes = plt.subplots(rows, cols, figsize=(8, 1.75 * rows + head), squeeze=False,
                             gridspec_kw={"wspace": 0.06, "hspace": 0.12})
    fig.subplots_adjust(left=0.005, right=0.995, bottom=0.01, top=1 - head / (1.75 * rows + head))
    fig.suptitle(title, x=0.01, y=0.995, ha="left", fontsize=11, fontweight="bold",
                 color=NAVY, fontfamily="DejaVu Serif")

    def tone(up):
        return GREEN if up is True else RED if up is False else GREY

    for ax, c in zip(axes.ravel(), cards):
        col = tone(c["up"])
        ax.set_facecolor(LIGHT)
        ax.grid(False)
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
        ys = c["ys"]
        if len(ys) >= 2:
            lo, hi = min(ys), max(ys)
            span = (hi - lo) or max(abs(hi), 1) * 0.05
            ax.set_ylim(lo - span * 0.2, hi + span * 2.2)
            xs = list(range(len(ys)))
            ax.set_xlim(0, len(ys) - 1)
            ax.fill_between(xs, ys, lo - span * 0.2, color=col, alpha=0.15)
            ax.plot(xs, ys, color=col, lw=1.8)
            ax.scatter([xs[-1]], [ys[-1]], color=col, s=14, zorder=3)
        else:
            ax.text(0.06, 0.12, "trend builds daily", transform=ax.transAxes,
                    fontsize=7, color=GREY, style="italic")
        ax.text(0.06, 0.93, c["label"], transform=ax.transAxes, fontsize=8, color=GREY,
                fontweight="bold", va="top")
        ax.text(0.06, 0.80, c["value"], transform=ax.transAxes, fontsize=13, color=NAVY,
                fontweight="bold", fontfamily="DejaVu Serif", va="top")
        for i, (txt, up) in enumerate(c["subs"]):
            ax.text(0.06 + i * 0.44, 0.57, txt, transform=ax.transAxes, fontsize=7.5,
                    color=tone(up), fontweight="bold", va="top")
    for ax in axes.ravel()[len(cards):]:
        ax.axis("off")
    return fig_to_image(fig)


# ------------------------------------------------------------------ 1. WEATHER
WMO = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
       51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain",
       65: "Heavy rain", 80: "Showers", 81: "Showers", 82: "Violent showers",
       95: "Thunderstorm", 96: "Thunderstorm + hail", 99: "Thunderstorm + hail"}


def get_weather():
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": LOCATION["lat"], "longitude": LOCATION["lon"], "timezone": "Africa/Nairobi",
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weather_code",
        "forecast_days": 1})
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------------ 2. STOCKS
class _RowParser(HTMLParser):
    """Collects <tr> rows as lists of cell texts. Tolerates missing closing tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell = [], None, None

    def _end_cell(self):
        if self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
        self._cell = None

    def _end_row(self):
        self._end_cell()
        if self._row:
            self.rows.append(self._row)
        self._row = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._end_row()
            self._row = []
        elif tag in ("td", "th"):
            self._end_cell()
            if self._row is None:
                self._row = []
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._end_cell()
        elif tag in ("tr", "table", "tbody", "thead"):
            self._end_row()

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_rows(html):
    p = _RowParser()
    p.feed(html)
    p.close()
    p._end_row()
    return p.rows


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("+", "").replace("%", "").strip())
    except Exception:
        return None


def _http_get(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}
    last_error = None
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=20, headers=headers)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last_error = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed after 3 attempts: {url}") from last_error


def _debug_dump(name, html, rows):
    (OUT_DIR / f"debug_{name}.html").write_text(html[:200000], encoding="utf-8")
    print(f"  ! {name}: {len(rows)} table rows parsed, sample: {rows[:2]}")


TICKER_RE = re.compile(r"[A-Z0-9\-]{2,8}")


def _nse_stockanalysis():
    """Columns: No. | Symbol | Company | Market Cap | Price | % Change | Revenue"""
    html = _http_get("https://stockanalysis.com/list/nairobi-stock-exchange/")
    rows = parse_rows(html)
    out = {}
    for c in rows:
        if len(c) >= 6 and TICKER_RE.fullmatch(c[1]):
            price = _num(c[4])
            if price:
                out[c[1]] = (price, _num(c[5]), c[2], None)       # (price, pct, name, volume)
    if len(out) < 10:
        _debug_dump("stockanalysis", html, rows)
    return out


def _nse_afx():
    """Columns: Ticker | Name | Volume | Price | Change (absolute)"""
    html = _http_get("https://afx.kwayisi.org/nseke")
    rows = parse_rows(html)
    out = {}
    for c in rows:
        if len(c) >= 4 and TICKER_RE.fullmatch(c[0]):
            price = _num(c[3])
            chg = _num(c[4]) if len(c) > 4 else None
            if price:
                pct = chg / (price - chg) * 100 if (chg is not None and price - chg > 0) else None
                out[c[0]] = (price, pct, c[1], _num(c[2]))       # (price, pct, name, volume)
    if len(out) < 10:
        _debug_dump("afx", html, rows)
    return out


NSE_SOURCE_NAMES = {"_nse_stockanalysis": "stockanalysis.com", "_nse_afx": "afx.kwayisi.org"}


def get_nse_table():
    """{ticker: (last_close, pct_change|None, company_name, volume|None)} from the first source that works."""
    for fn in (_nse_stockanalysis, _nse_afx):
        try:
            out = fn()
            watch_hits = sum(1 for sym in NSE_WATCHLIST.values() if out.get(sym, (None,))[0] is not None)
            if len(out) >= 10 and watch_hits:
                print(f"  NSE source {fn.__name__}: {len(out)} tickers, {watch_hits} watchlist matches")
                get_nse_table.source = NSE_SOURCE_NAMES[fn.__name__]
                return out
            print(f"  ! {fn.__name__}: unusable table ({len(out)} tickers, {watch_hits} watchlist matches)")
        except Exception as e:
            print(f"  ! {fn.__name__}: {e}")
    raise RuntimeError("all NSE sources failed")


def load_history(since=HISTORY_START):
    hist = defaultdict(dict)  # sym -> {date: price}
    if HISTORY_CSV.exists():
        with open(HISTORY_CSV, newline="") as f:
            for row in csv.DictReader(f):
                if since is None or row["date"] >= since:
                    hist[row["symbol"]][row["date"]] = float(row["price"])
    return hist


def save_history(hist):
    with open(HISTORY_CSV, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["date", "symbol", "price"])
        for sym, series in hist.items():
            for d, p in sorted(series.items()):
                wr.writerow([d, sym, p])


def collect_nse(table=None):
    # Preserve all collected dates so the watchlist trend can grow across runs.
    # The source parser already rejects malformed/currently unusable data.
    hist = load_history(since=None)
    if table is None:
        table = safe(get_nse_table, {}, "nse table")
    if not table:
        # Keep showing the last known counters during a temporary runner/source outage.
        # The rows are marked stale below; legacy prices are only used when no current
        # table was collected, so the normal HISTORY_START accuracy guard remains intact.
        hist = load_history(since=None)
        print("  ! NSE sources unavailable; using stored prices as stale fallback")
    rows = []
    for name, sym in NSE_WATCHLIST.items():
        price, pct_src, _, volume = table.get(sym, (None, None, None, None))
        if price is not None:
            hist[sym][TODAY] = price
        series = sorted(hist[sym].items())
        cur = series[-1][1] if series else None
        if pct_src is not None:
            pct = pct_src                              # last-session change from the source
        else:
            prev = series[-2][1] if len(series) >= 2 else None
            pct = ((cur - prev) / prev * 100) if (cur and prev) else None
        rows.append({"name": name, "sym": sym, "price": cur, "pct": pct, "volume": volume,
                     "fresh": price is not None, "series": series})
    if table or hist:
        save_history(hist)
    else:
        print("  ! No NSE data or history available; preserving existing history file")
    return rows


def nse_chart(rows):
    cards = []
    for r in rows:
        if r["price"] is None:
            subs = [("not found on NSE", None)]
        else:
            subs = [(fmt_pct(r["pct"]) + ("" if r["fresh"] else "  (stale)"),
                     None if r["pct"] is None else r["pct"] >= 0)]
        cards.append({"label": f"{r['sym']} | {r['name'][:18]}",
                      "value": "n/a" if r["price"] is None else f"KES {r['price']:.2f}",
                      "subs": subs, "ys": [p for _, p in r["series"]],
                      "up": None if r["pct"] is None else r["pct"] >= 0})
    return sparkline_grid(cards, "NSE watchlist - last close and trend")


def global_tech():
    if yf is None:
        return []
    out = []
    for name, sym in GLOBAL_TECH.items():
        try:
            df = yf.Ticker(sym).history(period="1mo").dropna(subset=["Close"])
            h = df["Close"]
            if len(h) >= 2:
                ohlc = [(d.strftime("%Y-%m-%d"), float(o), float(hi), float(lo), float(c), float(v))
                        for d, o, hi, lo, c, v in zip(df.index, df["Open"], df["High"], df["Low"],
                                                      df["Close"], df["Volume"])]
                out.append({"name": name, "sym": sym, "series": h, "ohlc": ohlc,
                            "price": float(h.iloc[-1]),
                            "pct": float((h.iloc[-1] / h.iloc[-2] - 1) * 100),
                            "pct30": float((h.iloc[-1] / h.iloc[0] - 1) * 100)})
        except Exception:
            continue
    return out


def global_chart(rows):
    cards = [{"label": f"{r['sym']} | {r['name']}", "value": f"${r['price']:.2f}",
              "subs": [(f"1d {fmt_pct(r['pct'])}", r["pct"] >= 0), (f"30d {fmt_pct(r['pct30'])}", r["pct30"] >= 0)],
              "ys": r["series"].tolist(), "up": r["pct30"] >= 0} for r in rows]
    return sparkline_grid(cards, "Big tech - 30-day trend")


# ------------------------------------------------------------------ 3. GITHUB
def gh(path, params=None):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "TechRadarAgent"}
    if GH_PAT:
        h["Authorization"] = f"Bearer {GH_PAT}"
    if GH_PAT:
        h["Authorization"] = "Bearer " + GH_PAT
    r = requests.get(f"https://api.github.com{path}", headers=h, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def push_details(repo, before, head):
    """Events no longer carry commits, so ask the API: (commit_count, latest_message)."""
    try:
        if before and set(before) != {"0"}:
            c = gh(f"/repos/{repo}/compare/{before}...{head}")
            msgs = [x["commit"]["message"].split("\n")[0] for x in c.get("commits", [])]
            return (c.get("total_commits") or len(msgs) or 1), (msgs[-1] if msgs else "")
        c = gh(f"/repos/{repo}/commits/{head}")
        return 1, c["commit"]["message"].split("\n")[0]
    except Exception:
        return 1, ""


def collect_github():
    user = gh(f"/users/{GITHUB_USER}")
    repos = gh(f"/users/{GITHUB_USER}/repos", {"per_page": 100, "sort": "pushed"})
    events = gh(f"/users/{GITHUB_USER}/events", {"per_page": 100})
    since = NOW - timedelta(days=14)
    per_day, pushes, repo_commits, lookups = Counter(), [], Counter(), 0
    for e in events:
        if e["type"] != "PushEvent":
            continue
        ts = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")).astimezone(TZ)
        if ts < since:
            continue
        p = e["payload"]
        if lookups < 25:
            n, msg = push_details(e["repo"]["name"], p.get("before"), p.get("head"))
            lookups += 1
        else:
            n, msg = p.get("size") or 1, ""
        per_day[ts.date()] += n
        repo_commits[e["repo"]["name"]] += n
        pushes.append((ts.strftime("%d %b %H:%M"), e["repo"]["name"], n, msg))
    langs = Counter(r["language"] for r in repos if r.get("language") and not r.get("fork"))
    return {"user": user, "repos": repos, "per_day": per_day, "pushes": pushes[:10],
            "repo_commits": repo_commits, "langs": langs,
            "stars": sum(r["stargazers_count"] for r in repos),
            "total14": sum(per_day.values())}


def github_charts(g):
    days = [(NOW - timedelta(days=i)).date() for i in range(13, -1, -1)]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.4, 3.2), gridspec_kw={"width_ratios": [2.2, 1]})
    vals = [g["per_day"].get(d, 0) for d in days]
    peak = max(vals) if vals else 0
    xs = list(range(len(days)))
    a1.bar(xs, vals, width=0.7, color=[GREEN if (v == peak and v > 0) else "#A7F3D0" for v in vals])
    for x, v in zip(xs, vals):
        if v:
            a1.text(x, v + peak * 0.03, str(v), ha="center", fontsize=7, color=NAVY, fontweight="bold")
    a1.set_xticks(xs)
    a1.set_xticklabels([d.strftime("%d %b") for d in days], rotation=60, fontsize=7)
    a1.grid(False, axis="x")
    a1.spines["left"].set_visible(False)
    a1.set_yticks([])
    ttl(a1, "Commits pushed - last 14 days", fontsize=10)
    top = g["langs"].most_common(6)
    if top:
        a2.pie([c for _, c in top], labels=[l for l, _ in top], colors=PALETTE,
               textprops={"fontsize": 7}, wedgeprops={"width": 0.38, "edgecolor": "white"})
        a2.text(0, 0, f"{sum(g['langs'].values())}\nrepos", ha="center", va="center",
                fontsize=9, fontweight="bold", color=NAVY, fontfamily="DejaVu Serif")
        ttl(a2, "Repo languages", fontsize=10)
    a2.grid(False)
    fig.tight_layout()
    return fig_to_image(fig)


# ------------------------------------------------------------------ 4. NEWS + REDDIT
def _plain(html_text, limit=300):
    """Feed summaries are HTML; keep the publisher's own text, trimmed at a word boundary."""
    txt = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_text or ""))).strip()
    txt = re.sub(r"\s*(The post .*? appeared first on .*|Continue reading.*|Read more.*)$", "", txt)
    if len(txt) > limit:
        txt = txt[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return txt


def _feed_item(e, feed):
    pub = None
    stamp = e.get("published_parsed") or e.get("updated_parsed")
    if stamp:
        pub = datetime(*stamp[:6], tzinfo=ZoneInfo("UTC")).astimezone(TZ).isoformat()
    title = e.title.strip()
    source = (e.get("source") or {}).get("title") or feed.feed.get("title") or ""
    if source and title.endswith(f" - {source}"):            # Google News appends " - Publisher"
        title = title[: -len(source) - 3]
    summary = _plain(e.get("summary"))
    if summary.lower().startswith(title.lower()[:40]):      # aggregators repeat the headline
        summary = ""
    return {"title": title, "url": e.get("link", ""), "source": source.strip(), "published": pub,
            "summary": summary}


def collect_news(per_section=6):
    out = {}
    for section, urls in NEWS_FEEDS.items():
        items, seen = [], set()
        for u in urls:
            try:
                feed = feedparser.parse(requests.get(u, headers={"User-Agent": "TechRadarAgent/2.0"},
                                                     timeout=10).content)
                for e in feed.entries[:5]:
                    if e.get("title") and e.title not in seen:
                        seen.add(e.title)
                        items.append(_feed_item(e, feed))
            except Exception:
                continue
        items.sort(key=lambda i: i["published"] or "", reverse=True)   # newest first
        out[section] = items[:per_section]
    return out


def _luma_event(x):
    ev, tk = x.get("event") or {}, x.get("ticket_info") or {}
    geo = ev.get("geo_address_info") or {}
    price = tk.get("price") or {}
    price_text = None
    if price.get("cents") is not None:
        price_text = f"{price.get('currency', '').upper()} {price['cents'] / 100:,.0f}".strip()
    hosts = [h.get("name") for h in x.get("hosts") or [] if h.get("name")]
    return {"name": ev.get("name", "").strip(), "url": f"https://lu.ma/{ev.get('url', '')}",
            "start": ev.get("start_at"), "end": ev.get("end_at"), "location_type": ev.get("location_type"),
            "city": geo.get("city"), "area": geo.get("sublocality") or geo.get("address"),
            "organizer": (x.get("calendar") or {}).get("name") or ", ".join(hosts[:2]),
            "cover": ev.get("cover_url"), "is_free": tk.get("is_free"), "price_text": price_text,
            "guests": x.get("guest_count"), "availability": x.get("registration_availability"),
            "near_capacity": tk.get("is_near_capacity"), "sold_out": tk.get("is_sold_out"),
            "spots": tk.get("spots_remaining"), "approval": tk.get("require_approval"), "source": "Luma"}


def collect_events():
    """Upcoming Nairobi events from Luma's public discover feed (falls back to the city page's
    embedded data). Times are converted to Nairobi time. Nothing is inferred beyond what Luma lists."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TechRadarAgent/1.0; personal use)"}
    entries = []
    try:
        r = requests.get("https://api.lu.ma/discover/get-paginated-events", headers=headers, timeout=15,
                         params={"discover_place_api_id": LUMA_PLACE_ID, "pagination_limit": 50})
        r.raise_for_status()
        entries = r.json().get("entries", [])
    except (requests.RequestException, ValueError) as e:
        print(f"  ! luma api: {e}")
    if not entries:
        html = _http_get("https://lu.ma/nairobi")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            entries = (json.loads(m.group(1)).get("props", {}).get("pageProps", {})
                       .get("initialData", {}).get("data", {}).get("events", []))
    out = []
    for x in entries:
        ev = _luma_event(x)
        for k in ("start", "end"):
            if ev[k]:
                ev[k] = datetime.fromisoformat(ev[k].replace("Z", "+00:00")).astimezone(TZ).isoformat()
        if ev["name"] and ev["start"]:
            out.append(ev)
    return out


def _sub_of(url):
    m = re.search(r"/r/([^/]+)/", url or "")
    return m.group(1).lower() if m else ""


def collect_reddit():
    """One combined RSS request (avoids 429s), per-sub fallback, then last-good cache."""
    cache = {}
    if REDDIT_CACHE.exists():
        try:
            cache = json.loads(REDDIT_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TechRadarAgent/1.0; personal use)"}
    by_sub = defaultdict(list)
    combined_ok = False
    try:
        r = requests.get("https://www.reddit.com/r/" + "+".join(REDDIT_LANGS.values()) + "/top.rss",
                         params={"t": "day", "limit": 100}, headers=headers, timeout=15)
        r.raise_for_status()
        for e in feedparser.parse(r.content).entries:
            by_sub[_sub_of(e.link)].append({"title": e.title, "url": e.link, "score": None, "comments": None})
        combined_ok = bool(by_sub)
    except Exception as e:
        print(f"  ! reddit combined: {e}")

    out = {}
    for label, sub in REDDIT_LANGS.items():
        posts = by_sub.get(sub.lower(), [])[:5]
        if not posts and combined_ok:                 # combined worked but this sub was too quiet
            try:
                time.sleep(4)
                r = requests.get(f"https://www.reddit.com/r/{sub}/top.rss?t=day", headers=headers, timeout=10)
                r.raise_for_status()
                posts = [{"title": e.title, "url": e.link, "score": None, "comments": None}
                         for e in feedparser.parse(r.content).entries[:5]]
            except Exception as e:
                print(f"  ! reddit r/{sub}: {e}")
        if posts:
            cache[label] = posts
        elif cache.get(label):
            print(f"  ... r/{sub}: using cached posts")
            posts = cache[label]
        out[label] = posts
    REDDIT_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return out


def reddit_chart(reddit):
    if not any(p["score"] is not None for v in reddit.values() for p in v):
        return None
    labels = [k for k, v in reddit.items() if v]
    scores = [sum(p["score"] or 0 for p in reddit[k]) for k in labels]
    comments = [sum(p["comments"] or 0 for p in reddit[k]) for k in labels]
    fig, ax = plt.subplots(figsize=(8, 2.8))
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], scores, 0.4, color=BLUE, label="Upvotes (top 5)")
    ax.bar([i + 0.2 for i in x], comments, 0.4, color="#F59E0B", label="Comments (top 5)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ttl(ax, "Reddit buzz by language - today")
    ax.legend(frameon=False, fontsize=8)
    return fig_to_image(fig)


# ------------------------------------------------------------------ 5. ALERTS
def _load_alert_state():
    if not ALERT_STATE.exists():
        return {"last_run": None, "seen": []}
    try:
        state = json.loads(ALERT_STATE.read_text(encoding="utf-8"))
        return {"last_run": state.get("last_run"), "seen": list(state.get("seen", []))[-500:]}
    except (OSError, ValueError) as e:
        print(f"  ! alert state could not be read: {e}")
        return {"last_run": None, "seen": []}


def _save_alert_state(state):
    ALERT_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _add_alert(alerts, state, alert):
    if alert["id"] not in state["seen"]:
        alerts.append(alert)


def collect_alerts(state):
    alerts = []
    since = (datetime.fromisoformat(state["last_run"]) if state["last_run"]
             else NOW - timedelta(hours=1))

    if GH_PAT:
        notifications = safe(
            lambda: gh("/notifications", {"all": "false", "per_page": 50}),
            [], "github notifications")
        for item in notifications:
            updated = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")).astimezone(TZ)
            if updated <= since or item.get("reason") not in {
                "mention", "assign", "review_requested", "security_alert"
            }:
                continue
            subject = item.get("subject", {})
            repo = item.get("repository", {}).get("full_name", "")
            _add_alert(alerts, state, {
                "id": f"notification:{item['id']}",
                "title": f"GitHub {item['reason'].replace('_', ' ')}: {subject.get('title', 'notification')}",
                "url": f"https://github.com/{repo}",
            })

    for event in safe(lambda: gh(f"/users/{GITHUB_USER}/events", {"per_page": 100}), [], "github activity"):
        created = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")).astimezone(TZ)
        if created <= since:
            continue
        event_type = event.get("type")
        repo = event.get("repo", {}).get("name", "")
        payload = event.get("payload", {})
        if event_type == "ReleaseEvent":
            release = payload.get("release", {})
            _add_alert(alerts, state, {
                "id": f"release:{release.get('id', event['id'])}",
                "title": f"New release in {repo}: {release.get('name') or release.get('tag_name', 'release')}",
                "url": release.get("html_url", f"https://github.com/{repo}/releases"),
            })
        elif event_type in {"IssuesEvent", "IssueCommentEvent", "PullRequestEvent"}:
            issue = payload.get("issue") or payload.get("pull_request") or {}
            _add_alert(alerts, state, {
                "id": f"github:{event['id']}",
                "title": f"GitHub activity in {repo}: {issue.get('title', event_type)}",
                "url": issue.get("html_url", f"https://github.com/{repo}"),
            })
        elif event_type == "PushEvent" and repo.lower().startswith(f"{GITHUB_USER.lower()}/"):
            _add_alert(alerts, state, {
                "id": f"push:{event['id']}",
                "title": f"New push to {repo} ({payload.get('size', 1)} commit(s))",
                "url": f"https://github.com/{repo}/commits/{payload.get('head', '')}",
            })

    for row in safe(collect_nse, [], "nse alerts"):
        if row["pct"] is not None and abs(row["pct"]) >= ALERT_MOVE_THRESHOLD:
            _add_alert(alerts, state, {
                "id": f"nse:{TODAY}:{row['sym']}:{row['pct']:.2f}",
                "title": f"NSE move: {row['sym']} {fmt_pct(row['pct'])}",
                "url": "https://www.nse.co.ke/",
            })
    for row in safe(global_tech, [], "global market alerts"):
        if abs(row["pct"]) >= ALERT_MOVE_THRESHOLD:
            _add_alert(alerts, state, {
                "id": f"global:{TODAY}:{row['sym']}:{row['pct']:.2f}",
                "title": f"Global tech move: {row['sym']} {fmt_pct(row['pct'])}",
                "url": f"https://finance.yahoo.com/quote/{row['sym']}",
            })

    news = safe(lambda: collect_news(per_section=10), {}, "keyword news")
    for section_name, items in news.items():
        for item in items:
            if any(k in item["title"].lower() for k in ALERT_KEYWORDS):
                _add_alert(alerts, state, {
                    "id": f"news:{item['url']}",
                    "title": f"{section_name}: {item['title']}",
                    "url": item["url"],
                })
    reddit = safe(collect_reddit, {}, "reddit alerts")
    for section_name, items in reddit.items():
        for item in items:
            if any(k in item["title"].lower() for k in ALERT_KEYWORDS):
                _add_alert(alerts, state, {
                    "id": f"reddit:{item['url']}",
                    "title": f"Reddit {section_name}: {item['title']}",
                    "url": item["url"],
                })
    return alerts


def send_alert_email(body):
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not pw:
        print("GMAIL_APP_PASSWORD not set - email alert skipped.")
        return
    msg = MIMEMultipart()
    msg["Subject"] = f"TechWatch alert - {TODAY_PRETTY}"
    msg["From"], msg["To"] = SENDER_EMAIL, RECIPIENT_EMAIL
    msg.attach(MIMEText(body.replace("\n", "<br>"), "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as srv:
            srv.login(SENDER_EMAIL, pw)
            srv.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print("Alert email sent.")
    except (OSError, smtplib.SMTPException) as e:
        print(f"Alert email failed: {e}")


def send_telegram(body):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not set - Telegram alert skipped.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": body[:4000], "disable_web_page_preview": True},
            timeout=15,
        )
        r.raise_for_status()
        print("Telegram alert sent.")
    except requests.RequestException as e:
        print(f"Telegram alert failed: {e}")


def run_alerts():
    state = _load_alert_state()
    alerts = collect_alerts(state)
    if alerts:
        body = "\n".join(
            [f"TechWatch alerts - {TODAY_PRETTY}", ""] +
            [f"- {a['title']}\n  {a['url']}" for a in alerts[:20]]
        )
        send_alert_email(body)
        send_telegram(body)
        state["seen"] = (state["seen"] + [a["id"] for a in alerts])[-500:]
    else:
        print("No new alerts.")
    state["last_run"] = NOW.isoformat()
    _save_alert_state(state)


def takeaways(section, items):
    if not items:
        return ""
    heads = "\n".join(f"- {i['title']}" for i in items)
    return llm("You are a concise tech analyst. Reply with exactly 3 short bullet lines "
               "(start each with '- ', max 20 words each) on what matters most. No preamble.",
               f"Section: {section}\nHeadlines:\n{heads}", 200)


def _json_array(text):
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def annotate_news(section_name, items):
    """Adds 'why' (one sentence) and 'impact' (high/medium/low) to each item, in place.
    Shown in the reports under an 'AI analysis' label, never as fact."""
    if not items:
        return
    listing = "\n".join(f"{i}. {it['title']} | {it.get('summary', '')[:220]}" for i, it in enumerate(items))
    out = llm("You are a sharp technology and markets analyst briefing a Nairobi software engineer who "
              "also invests. For each numbered item, explain in ONE sentence (max 28 words) why it matters "
              "and rate its impact as high, medium or low. Use only the information given; do not add "
              "facts, figures or names that are not in the item. Reply with ONLY a JSON array like "
              '[{"i":0,"why":"...","impact":"medium"}].',
              f"Section: {section_name}\n{listing}", 900)
    for row in _json_array(out):
        try:
            it = items[int(row["i"])]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if isinstance(row.get("why"), str):
            it["why"] = row["why"].strip()
        if str(row.get("impact", "")).lower() in ("high", "medium", "low"):
            it["impact"] = row["impact"].lower()


def market_commentary(nse, glob, news):
    facts = [f"{r['sym']} ({r['name']}): ${r['price']:.2f}, 1d {fmt_pct(r['pct'])}, 30d {fmt_pct(r['pct30'])}"
             for r in glob]
    facts += [f"NSE {r['sym']} ({r['name']}): KES {r['price']:.2f}, {fmt_pct(r['pct'])}"
              for r in nse if r["price"] is not None]
    if not facts:
        return ""
    heads = [i["title"] for k in ("Tech Giants", "Fintech") for i in news.get(k, [])]
    return llm("You are a markets analyst. Write exactly 4 bullet lines ('- ', max 30 words each): best and "
               "worst performer, what may have influenced moves (ONLY if a headline supports it, otherwise say "
               "the cause is not visible in today's news), overall sentiment, and one risk or opportunity to "
               "investigate. Never invent numbers or events. No preamble.",
               "Market data:\n" + "\n".join(facts) + "\n\nToday's headlines:\n" + "\n".join(heads[:12]), 320)


# ------------------------------------------------------------------ 5. PDF BUILD
ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], fontName=SERIF_B, fontSize=26,
                    textColor=colors.white, alignment=0, leading=30)
SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontName=SANS, fontSize=10, textColor=colors.HexColor("#CBD5E1"))
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName=SERIF_B, fontSize=16,
                    textColor=colors.HexColor(NAVY), spaceBefore=12, spaceAfter=2)
H3 = ParagraphStyle("H3", parent=ss["Heading3"], fontName=SANS_B, fontSize=10.5,
                    textColor=colors.HexColor(BLUE), spaceBefore=8, spaceAfter=3)
BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontName=SANS, fontSize=9, leading=13,
                      textColor=colors.HexColor("#334155"))
SMALL = ParagraphStyle("SMALL", parent=BODY, fontSize=8, textColor=colors.HexColor(GREY))
BUL = ParagraphStyle("BUL", parent=BODY, leftIndent=10, bulletIndent=0, spaceAfter=2)


def section(title, need_mm=70):
    """Heading + rule; CondPageBreak moves it to a new page only if little room is left."""
    return [CondPageBreak(need_mm * mm), P(title, H2),
            HRFlowable(width="100%", thickness=1.4, color=colors.HexColor(BLUE), spaceBefore=0, spaceAfter=6)]


def banner(title, subtitle):
    t = Table([[Paragraph(escape(title), H1)], [Paragraph(escape(subtitle), SUB)]], colWidths=[180 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(NAVY)),
                           ("LEFTPADDING", (0, 0), (-1, -1), 14), ("TOPPADDING", (0, 0), (0, 0), 16),
                           ("BOTTOMPADDING", (0, -1), (-1, -1), 14),
                           ("LINEBELOW", (0, -1), (-1, -1), 4, colors.HexColor(BLUE))]))
    return t


def kpi_row(items):
    cells = [[Paragraph(f'<font name="{SERIF_B}" size="15" color="{NAVY}">{escape(str(v))}</font><br/>'
                        f'<font name="{SANS}" size="7" color="{GREY}">{escape(k.upper())}</font>', BODY)
              for k, v in items]]
    t = Table(cells, colWidths=[180 * mm / len(items)] * len(items))
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(LIGHT)),
                           ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                           ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return t


def data_table(header, rows, col_widths, mono_cols=()):
    t = Table([header] + rows, colWidths=[c * mm for c in col_widths], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
             ("FONTNAME", (0, 0), (-1, 0), SANS_B), ("FONTNAME", (0, 1), (-1, -1), SANS),
             ("FONTSIZE", (0, 0), (-1, -1), 8.5),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(LIGHT)]),
             ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    for c in mono_cols:
        style.append(("FONTNAME", (c, 1), (c, -1), MONO))
    t.setStyle(TableStyle(style))
    return t


def fmt_pct(p):
    return "n/a" if p is None else f"{p:+.2f}%"


def trunc(s, n):
    s = latin(s or "")
    return s if len(s) <= n else s[:n - 1] + "..."


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(SANS, 7.5)
    canvas.setFillColor(colors.HexColor(GREY))
    canvas.drawString(15 * mm, 8 * mm, f"TechWatch Daily Intelligence - {TODAY_PRETTY} - AI by {LLM_NAME} - not investment advice")
    canvas.drawRightString(195 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def bullet(text):
    return Paragraph(escape(latin(text.lstrip("-*• ").strip())), BUL, bulletText="•")


def pill(text, hex_color):
    return f'<font name="{SANS_B}" size="7" color="{hex_color}">{escape(text.upper())}</font>'


def callout(flowables, accent=BLUE, bg="#F5F3FF"):
    """Tinted box with a coloured left rule (used for AI-labelled and 'not connected' content)."""
    t = Table([[flowables]], colWidths=[180 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
                           ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(accent)),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return t


def news_block(it, show_desk=True):
    col = report_html.DESK_HEX.get(it["desk"], BLUE)
    flags = []
    if "breaking" in it["flags"]:
        flags.append(pill("Breaking", RED))
    if "high" in it["flags"]:
        flags.append(pill("High priority", "#B45309"))
    if "funding" in it["flags"]:
        flags.append(pill("Funding / M&A", "#B45309"))
    head = (f'<font name="{SANS_B}" size="7" color="{col}">{escape(it["desk"].upper())}</font>   ' if show_desk else "")
    head += "   ".join(flags)
    meta = f'{escape(latin(it.get("source") or ""))} · {escape(report_html.ago(it["dt"], NOW))}'
    out = [Paragraph(head, SMALL)] if head.strip() else []
    out.append(Paragraph(f'<font name="{SANS_B}" size="10">{link_markup(it["title"], it.get("url", ""))}</font>', BODY))
    out.append(Paragraph(meta, SMALL))
    if it.get("why"):
        out.append(Paragraph(f'<font color="{PURPLE}"><b>Why it matters (AI):</b></font> {escape(latin(it["why"]))}', BODY))
    out.append(Spacer(1, 6))
    return KeepTogether(out)


def build_pdf(path, weather, nse, glob, gh_data, news, reddit, brief, news_take, ctx=None, prep=None):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=12 * mm, bottomMargin=16 * mm, title="TechWatch Daily Intelligence")
    ctx = ctx or {}
    prep = prep or {"items": [], "events": [], "ops": []}
    s = [banner("TechWatch · Daily Intelligence",
                f"{TODAY_PRETTY}  |  {LOCATION['name']}  |  Generated {NOW.strftime('%H:%M')} EAT"), Spacer(1, 8)]

    # 1. Executive briefing
    if brief:
        s += section("1  Executive briefing", 40)
        s.append(callout([bullet(l) for l in brief.splitlines() if l.strip()]
                         + [P(f"AI summary of the data in this report ({LLM_NAME}).", SMALL)], PURPLE))
    if weather:
        c, d = weather["current"], weather["daily"]
        s += [Spacer(1, 6), P(f"{LOCATION['name']} weather - today", H3),
              kpi_row([("Now", f"{c['temperature_2m']:.0f} C"),
                       ("High / Low", f"{d['temperature_2m_max'][0]:.0f} / {d['temperature_2m_min'][0]:.0f} C"),
                       ("Conditions", WMO.get(c["weather_code"], "-")),
                       ("Rain chance", f"{d['precipitation_probability_max'][0]}%"),
                       ("Wind", f"{c['wind_speed_10m']:.0f} km/h")])]

    # 2. Top stories (ranked)
    if prep["items"]:
        s += section("2  Top technology news", 60)
        s.append(P("Ranked by AI-estimated impact, recency, your alert keywords and Kenyan relevance.", SMALL))
        s.append(Spacer(1, 4))
        s += [news_block(it) for it in prep["items"][:6]]

    # 4. Nairobi events
    s += section("4  Nairobi technology events", 50)
    if prep["events"]:
        rows = []
        for ev in sorted(prep["events"], key=lambda x: (-x["register_now"], x["dt"]))[:12]:
            price = "Free" if ev.get("is_free") else (ev.get("price_text") or "Paid")
            reg = "REGISTER SOON" if ev["register_now"] else (ev.get("availability") or "-")
            rows.append([Paragraph(ev["dt"].strftime("%a %d %b<br/>%H:%M"), SMALL),
                         Paragraph(link_markup(ev["name"], ev["url"]) + "<br/>"
                                   + escape(latin(ev.get("organizer") or "")), SMALL),
                         Paragraph("Online" if ev["online"] else escape(latin(ev.get("area") or ev.get("city") or "Nairobi")), SMALL),
                         price, Paragraph(f'<font color="{RED if ev["register_now"] else GREY}"><b>{escape(reg)}</b></font>', SMALL)])
        s.append(data_table(["When", "Event / organizer", "Where", "Price", "Registration"], rows, [22, 80, 32, 20, 26]))
        s.append(P("Source: Luma public Nairobi feed. Instagram, WhatsApp, LinkedIn and Microsoft Events are not connected yet.", SMALL))
    else:
        s.append(P("No upcoming events returned by Luma this run.", SMALL))

    # 7-8. Markets
    s += section("7-8  Markets", 90)
    if nse:
        ch = nse_chart(nse)
        if ch:
            s.append(ch)
        missing = [r["sym"] for r in nse if r["price"] is None]
        if len(missing) == len(nse):
            note = "NSE price sources were unreachable for this run."
        else:
            note = f"NSE prices are end-of-day closes (not live), source: {ctx.get('nse_source', 'NSE table')}."
            if missing:
                note += f" No NSE listing found for: {', '.join(missing)}."
        note += " Dangote Petroleum Refinery is unlisted, so no price."
        s.append(P(note, SMALL))
    mk = [r for r in ctx.get("nse_market") or [] if r.get("pct") is not None]
    if mk:
        adv = sum(1 for r in mk if r["pct"] > 0)
        dec = sum(1 for r in mk if r["pct"] < 0)
        g = sorted([r for r in mk if r["pct"] > 0], key=lambda r: -r["pct"])[:5]
        l = sorted([r for r in mk if r["pct"] < 0], key=lambda r: r["pct"])[:5]
        s += [P(f"NSE breadth: {adv} advancing, {dec} declining, {len(ctx['nse_market']) - adv - dec} unchanged or not traded", H3)]
        rows = []
        for i in range(max(len(g), len(l))):
            a_ = g[i] if i < len(g) else None
            b_ = l[i] if i < len(l) else None
            rows.append([a_["sym"] if a_ else "", Paragraph(f'<font color="{GREEN}"><b>{fmt_pct(a_["pct"])}</b></font>', BODY) if a_ else "",
                         b_["sym"] if b_ else "", Paragraph(f'<font color="{RED}"><b>{fmt_pct(b_["pct"])}</b></font>', BODY) if b_ else ""])
        if rows:
            s.append(data_table(["Top gainer", "Change", "Top loser", "Change"], rows, [45, 45, 45, 45], mono_cols=(0, 2)))
    if glob:
        gc = global_chart(glob)
        if gc:
            s += [Spacer(1, 8), gc]
    if ctx.get("market_take"):
        s += [Spacer(1, 6), callout([P("Daily market analysis (AI interpretation, not investment advice)", H3)]
                                    + [bullet(l) for l in ctx["market_take"].splitlines() if l.strip()],
                                    PURPLE)]

    # 9-10. Not connected yet
    s += section("9-10  Dividends, corporate actions and MMFs", 40)
    s.append(callout([P("Not connected yet. No dividend, book-closure or MMF yield data is collected, so none is "
                        "shown. These sections fill only from official announcements and published yields and are never "
                        f"estimated. Funds being watched: {', '.join(MMF_WATCH)}.", BODY)], GREY, LIGHT))

    # GitHub (3. AI & developer)
    if gh_data:
        u = gh_data["user"]
        s += section(f"3  Developer activity - GitHub {GITHUB_USER}", 110) + [
            kpi_row([("Commits (14d)", gh_data["total14"]), ("Public repos", u["public_repos"]),
                     ("Stars", gh_data["stars"]), ("Followers", u["followers"])]),
            Spacer(1, 6), github_charts(gh_data)]
        if gh_data["pushes"]:
            s += [P("Recent pushes", H3),
                  data_table(["When", "Repo", "#", "Latest message"],
                             [[w, trunc(r.split("/")[-1], 26), n, trunc(m, 48)] for w, r, n, m in gh_data["pushes"]],
                             [28, 55, 10, 87], mono_cols=(0,))]

    # News by desk
    s += section("News coverage by desk", 60)
    for sec_name, items in news.items():
        col = report_html.DESK_HEX.get(sec_name, BLUE)
        block = [Paragraph(f'<font color="{col}">●</font> {escape(sec_name)}', H3)]
        if news_take.get(sec_name):
            block += [bullet(l) for l in news_take[sec_name].splitlines() if l.strip()]
        block.append(Spacer(1, 2))
        for i in items:
            when = report_html.ago(report_html.parse_dt(i.get("published")), NOW)
            block.append(Paragraph("&bull; " + link_markup(i["title"], i["url"])
                                   + f' <font color="{GREY}">· {escape(latin(i.get("source") or ""))} · {when}</font>', SMALL))
        s.append(KeepTogether(block))

    # 11. Reddit
    s += section("11  Reddit - community discussion (opinion, not fact)", 60)
    rc = reddit_chart(reddit)
    if rc:
        s.append(rc)
    for lang, posts in reddit.items():
        if posts:
            block = [P(lang, H3)] + [Paragraph("&bull; " + link_markup(p["title"], p["url"]), SMALL)
                                     for p in posts[:3]]
            s.append(KeepTogether(block))
    if not any(reddit.values()):
        s.append(P("Reddit blocked the request (common from cloud IPs). Try running locally.", SMALL))

    # 12. Opportunities
    if prep["ops"]:
        s += section("12  Opportunities worth watching", 40)
        for o in prep["ops"]:
            s.append(Paragraph(f'<font name="{SANS_B}" color="{PURPLE}">{escape(o["kind"].upper())}</font>  '
                               + link_markup(o["title"], o["url"] or "") + f'<br/><font color="{GREY}">{escape(latin(o["why"]))}</font>', BUL))
        s.append(P("Leads built by rules from this report's data, not recommendations.", SMALL))

    doc.build(s, onFirstPage=footer, onLaterPages=footer)


# ------------------------------------------------------------------ 6. EMAIL
def send_email(attachments, html_body):
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not pw:
        print("GMAIL_APP_PASSWORD not set - reports saved only.")
        return
    msg = MIMEMultipart()
    msg["Subject"] = f"TechWatch Daily Intelligence - {TODAY_PRETTY}"
    msg["From"], msg["To"] = SENDER_EMAIL, RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    for path, subtype in attachments:
        with open(path, "rb") as f:
            data = f.read()
        part = (MIMEText(data.decode("utf-8"), "html", "utf-8") if subtype == "html"
                else MIMEApplication(data, _subtype=subtype))
        part.add_header("Content-Disposition", "attachment", filename=Path(path).name)
        msg.attach(part)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(SENDER_EMAIL, pw)
            srv.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print("Email sent with " + ", ".join(f"{Path(p).name} ({Path(p).stat().st_size // 1024} KB)"
                                            for p, _ in attachments) + ".")
    except Exception as e:
        print(f"Email failed: {e}")


# ------------------------------------------------------------------ 7. ORCHESTRATOR
def _status(name, value, ok_detail, fail_detail):
    return {"name": name, "ok": bool(value), "detail": ok_detail if value else fail_detail}


def run_agent():
    print(f"Agent started - {LLM_NAME}")
    weather = safe(get_weather, None, "weather")
    nse_table = safe(get_nse_table, {}, "nse table")
    nse = safe(lambda: collect_nse(nse_table), [], "nse")
    glob = safe(global_tech, [], "global tech")
    gh_data = safe(collect_github, None, "github")
    news = safe(collect_news, {}, "news")
    reddit = safe(collect_reddit, {}, "reddit")
    events = safe(collect_events, [], "luma events")

    news_take = {sec: takeaways(sec, items) for sec, items in news.items()}
    for sec, items in news.items():
        annotate_news(sec, items)
    market_take = market_commentary(nse, glob, news)

    movers = ", ".join(f"{r['sym']} {fmt_pct(r['pct'])}" for r in nse if r["pct"] is not None)
    context = (f"Weather: {weather['current']['temperature_2m']:.0f}C "
               f"{WMO.get(weather['current']['weather_code'], '')}\n" if weather else "")
    context += f"NSE movers: {movers or 'no data'}\n"
    if glob:
        context += "US big tech 1d: " + ", ".join(f"{r['sym']} {fmt_pct(r['pct'])}" for r in glob) + "\n"
    if events:
        context += "Nairobi events (Luma): " + "; ".join(
            f"{e['name']} on {e['start'][:10]}" for e in events[:5]) + "\n"
    if gh_data:
        context += f"GitHub: {gh_data['total14']} commits in 14 days\n"
    context += "\n".join(f"{k}: " + "; ".join(i["title"] for i in v[:3]) for k, v in news.items())
    brief = llm("Write a morning executive brief for a Nairobi software engineer who also invests: exactly 5 "
                "bullet lines ('- '), max 26 words each, most important first. Cover the biggest tech story, "
                "markets, and any event worth attending. Use only the facts provided; never invent numbers. "
                "No preamble.", context, 320)

    nse_source = getattr(get_nse_table, "source", None)
    if not nse_source and any(row["price"] is not None for row in nse):
        nse_source = "stored history (stale)"
    ctx = {
        "now": NOW, "llm_name": LLM_NAME, "location": LOCATION["name"], "weather": weather, "wmo": WMO,
        "nse": nse, "glob": glob, "gh": gh_data, "github_user": GITHUB_USER, "news": news,
        "news_take": news_take, "reddit": reddit, "reddit_subs": REDDIT_LANGS, "brief": brief,
        "market_take": market_take, "events": events, "keywords": ALERT_KEYWORDS,
        "move_threshold": ALERT_MOVE_THRESHOLD, "mmf_watch": MMF_WATCH, "nse_source": nse_source,
        "nse_market": [{"sym": k, "price": v[0], "pct": v[1], "name": v[2], "volume": v[3]}
                       for k, v in nse_table.items()],
        "feed_sources": sorted({i["source"] for v in news.values() for i in v if i.get("source")}),
        "status": [
            _status("NSE prices", any(row["price"] is not None for row in nse),
                    f"{nse_source}: {sum(row['price'] is not None for row in nse)} watchlist counters",
                    "all NSE sources failed and no stored history"),
            _status("US big tech", glob, f"Yahoo Finance: {len(glob)} stocks", "yfinance unavailable"),
            _status("Tech news (RSS)", news and any(news.values()),
                    f"{sum(len(v) for v in news.values())} stories from {len(news)} desks", "all feeds failed"),
            _status("Nairobi events (Luma)", events, f"{len(events)} upcoming events", "Luma unavailable"),
            _status("Reddit", reddit and any(reddit.values()), f"{sum(len(v) for v in reddit.values())} posts",
                    "blocked / no posts"),
            _status("GitHub", gh_data, "activity, repos and commits", "GitHub API unavailable"),
            _status("Weather", weather, "Open-Meteo", "Open-Meteo unavailable"),
            _status("AI analysis", brief, LLM_NAME, f"{LLM_NAME} unavailable - reports show data only"),
            {"name": "Dividends & corporate actions", "ok": None, "detail": "not connected"},
            {"name": "MMF yields", "ok": None, "detail": "not connected"},
            {"name": "Instagram / WhatsApp / LinkedIn events", "ok": None, "detail": "not connected"},
        ],
    }
    prep = report_html.prepare(ctx)

    html_path = OUT_DIR / f"techwatch-{TODAY}.html"
    html_path.write_text(report_html.render_dashboard(ctx), encoding="utf-8")
    (OUT_DIR / "index.html").write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Dashboard built: {html_path}")

    pdf_path = OUT_DIR / f"tech-radar-{TODAY}.pdf"
    build_pdf(pdf_path, weather, nse, glob, gh_data, news, reddit, brief, news_take, ctx, prep)
    print(f"PDF built: {pdf_path}")

    send_email([(html_path, "html"), (pdf_path, "pdf")], report_html.render_email(ctx))


if __name__ == "__main__":
    run_alerts() if RUN_MODE == "alerts" else run_agent()