import os
import re
import io
import csv
import json
import time
import smtplib
from datetime import datetime, timedelta
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
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, PageBreak, KeepTogether, HRFlowable)

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

# NSE tickers as on afx.kwayisi.org/nseke. Dangote Petroleum Refinery is NOT listed on
# any exchange, so it cannot be priced. Quickmart (QMART) is not in the NSE listing either;
# it shows n/a and will appear automatically if it ever lists.
NSE_WATCHLIST = {
    "Co-op Bank": "COOP",
    "Stanbic Holdings": "SBIC",
    "EABL": "EABL",
    "BK Group": "BKG",
    "Eaagads": "EGAD",          # Eveready East Africa is "EVRD"
    "Quickmart": "QMART",
}
NSE_URL = "https://afx.kwayisi.org/nseke"
HISTORY_START = "2026-11-10"  # ignore stored prices before this (older scraper was inaccurate)

GLOBAL_TECH = {"NVIDIA": "NVDA", "Microsoft": "MSFT", "Apple": "AAPL",
               "Alphabet": "GOOGL", "Amazon": "AMZN", "Meta": "META"}

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
HISTORY_CSV = DATA_DIR / "nse_history.csv"
REDDIT_CACHE = DATA_DIR / "reddit_cache.json"
OUT_DIR = Path("output")
OUT_DIR.mkdir(exist_ok=True)

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

# ------------------------------------------------------------------ LLM
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if GROQ_API_KEY:
    client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)
    MODEL_NAME, LLM_NAME = "llama-3.3-70b-versatile", "Groq Llama 3.3 70B"
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
NAVY, BLUE, GREEN, RED, GREY, LIGHT = "#0F172A", "#2563EB", "#10B981", "#EF4444", "#64748B", "#F1F5F9"
PALETTE = [BLUE, GREEN, "#F59E0B", "#8B5CF6", "#EC4899", "#14B8A6", "#F97316"]

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
    """Card grid: label, big value, change text, mini trend line. Works from day one."""
    if not cards:
        return None
    rows = (len(cards) + cols - 1) // cols
    head = 0.55
    fig, axes = plt.subplots(rows, cols, figsize=(8, 1.75 * rows + head), squeeze=False,
                             gridspec_kw={"wspace": 0.06, "hspace": 0.12})
    fig.subplots_adjust(left=0.005, right=0.995, bottom=0.01, top=1 - head / (1.75 * rows + head))
    fig.suptitle(title, x=0.01, y=0.995, ha="left", fontsize=11, fontweight="bold",
                 color=NAVY, fontfamily="DejaVu Serif")
    for ax, c in zip(axes.ravel(), cards):
        col = GREEN if c["up"] is True else RED if c["up"] is False else GREY
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
            ax.text(0.06, 0.12, "trend starts after day 2", transform=ax.transAxes,
                    fontsize=7, color=GREY, style="italic")
        ax.text(0.06, 0.93, c["label"], transform=ax.transAxes, fontsize=8, color=GREY,
                fontweight="bold", va="top")
        ax.text(0.06, 0.80, c["value"], transform=ax.transAxes, fontsize=13, color=NAVY,
                fontweight="bold", fontfamily="DejaVu Serif", va="top")
        ax.text(0.06, 0.57, c["sub"], transform=ax.transAxes, fontsize=7.5, color=col,
                fontweight="bold", va="top")
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
def _num(s):
    try:
        return float(s.replace(",", "").replace("+", "").strip())
    except Exception:
        return None


def get_nse_table():
    """One request -> {ticker: (last_close, abs_change)} for every NSE stock.
    Source: afx.kwayisi.org/nseke (table: Ticker, Name, Volume, Price, Change)."""
    r = requests.get(NSE_URL, timeout=20, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
    r.raise_for_status()
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if len(cells) < 4 or not re.fullmatch(r"[A-Z0-9\-]{2,8}", cells[0]):
            continue
        price = _num(cells[3])
        chg = _num(cells[4]) if len(cells) > 4 else None
        if price:
            out[cells[0]] = (price, chg)
    print(f"  NSE table: parsed {len(out)} tickers")
    if len(out) < 10:
        raise RuntimeError("NSE table parse looks wrong (site layout changed or blocked)")
    return out


def load_history():
    hist = defaultdict(dict)  # sym -> {date: price}
    if HISTORY_CSV.exists():
        with open(HISTORY_CSV, newline="") as f:
            for row in csv.DictReader(f):
                if row["date"] >= HISTORY_START:
                    hist[row["symbol"]][row["date"]] = float(row["price"])
    return hist


def save_history(hist):
    with open(HISTORY_CSV, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["date", "symbol", "price"])
        for sym, series in hist.items():
            for d, p in sorted(series.items()):
                wr.writerow([d, sym, p])


def collect_nse():
    hist = load_history()
    table = safe(get_nse_table, {}, "nse table")
    rows = []
    for name, sym in NSE_WATCHLIST.items():
        price, chg = table.get(sym, (None, None))
        if price is not None:
            hist[sym][TODAY] = price
        series = sorted(hist[sym].items())
        cur = series[-1][1] if series else None
        if price is not None and chg is not None and price - chg > 0:
            pct = chg / (price - chg) * 100          # last-session change from the source
        else:
            prev = series[-2][1] if len(series) >= 2 else None
            pct = ((cur - prev) / prev * 100) if (cur and prev) else None
        rows.append({"name": name, "sym": sym, "price": cur, "pct": pct,
                     "fresh": price is not None, "series": series})
    save_history(hist)
    return rows


def nse_chart(rows):
    cards = []
    for r in rows:
        sub = "no change data yet" if r["pct"] is None else fmt_pct(r["pct"])
        if not r["fresh"]:
            sub += "  (stale)" if r["price"] is not None else ""
        cards.append({"label": f"{r['sym']} | {r['name'][:18]}",
                      "value": "n/a" if r["price"] is None else f"KES {r['price']:.2f}",
                      "sub": sub, "ys": [p for _, p in r["series"]],
                      "up": None if r["pct"] is None else r["pct"] >= 0})
    return sparkline_grid(cards, "NSE watchlist - trend since tracking began")


def global_tech():
    if yf is None:
        return []
    out = []
    for name, sym in GLOBAL_TECH.items():
        try:
            h = yf.Ticker(sym).history(period="1mo")["Close"].dropna()
            if len(h) >= 2:
                out.append({"name": name, "sym": sym, "series": h,
                            "price": float(h.iloc[-1]),
                            "pct": float((h.iloc[-1] / h.iloc[-2] - 1) * 100),
                            "pct30": float((h.iloc[-1] / h.iloc[0] - 1) * 100)})
        except Exception:
            continue
    return out


def global_chart(rows):
    cards = [{"label": f"{r['sym']} | {r['name']}", "value": f"${r['price']:.2f}",
              "sub": f"1d {fmt_pct(r['pct'])}   30d {fmt_pct(r['pct30'])}",
              "ys": r["series"].tolist(), "up": r["pct30"] >= 0} for r in rows]
    return sparkline_grid(cards, "Big tech - 30-day trend")


# ------------------------------------------------------------------ 3. GITHUB
def gh(path, params=None):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "TechRadarAgent"}
    if GH_PAT:
        h["Authorization"] = f"Bearer {GH_PAT}"
    r = requests.get(f"https://api.github.com{path}", headers=h, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def collect_github():
    user = gh(f"/users/{GITHUB_USER}")
    repos = gh(f"/users/{GITHUB_USER}/repos", {"per_page": 100, "sort": "pushed"})
    events = gh(f"/users/{GITHUB_USER}/events", {"per_page": 100})
    since = NOW - timedelta(days=14)
    per_day, pushes, repo_commits = Counter(), [], Counter()
    for e in events:
        if e["type"] != "PushEvent":
            continue
        ts = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")).astimezone(TZ)
        if ts < since:
            continue
        n = e["payload"].get("size") or len(e["payload"].get("commits", [])) or 1
        per_day[ts.date()] += n
        repo_commits[e["repo"]["name"]] += n
        msgs = [c["message"].split("\n")[0] for c in e["payload"].get("commits", [])]
        pushes.append((ts.strftime("%d %b %H:%M"), e["repo"]["name"], n, msgs[0] if msgs else ""))
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
                        items.append({"title": e.title, "url": e.get("link", "")})
            except Exception:
                continue
        out[section] = items[:per_section]
    return out


def collect_reddit():
    """RSS with pacing, retry/backoff on 429, and last-good cache fallback."""
    cache = {}
    if REDDIT_CACHE.exists():
        try:
            cache = json.loads(REDDIT_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TechRadarAgent/1.0; personal use)"}
    out = {}
    for label, sub in REDDIT_LANGS.items():
        posts = []
        for attempt in range(1, 4):
            try:
                r = requests.get(f"https://www.reddit.com/r/{sub}/top.rss?t=day",
                                 headers=headers, timeout=10)
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", 5 * attempt))
                    print(f"  ... r/{sub} rate-limited, waiting {min(wait, 30)}s")
                    time.sleep(min(wait, 30))
                    continue
                r.raise_for_status()
                feed = feedparser.parse(r.content)
                posts = [{"title": e.title, "url": e.link, "score": None, "comments": None}
                         for e in feed.entries[:5]]
                break
            except Exception as e:
                print(f"  ! reddit r/{sub}: {e}")
                break
        if posts:
            cache[label] = posts
        elif cache.get(label):
            print(f"  ... r/{sub}: using cached posts")
            posts = cache[label]
        out[label] = posts
        time.sleep(3)  # pace requests
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


def takeaways(section, items):
    if not items:
        return ""
    heads = "\n".join(f"- {i['title']}" for i in items)
    return llm("You are a concise tech analyst. Reply with exactly 3 short bullet lines "
               "(start each with '- ', max 20 words each) on what matters most. No preamble.",
               f"Section: {section}\nHeadlines:\n{heads}", 200)


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


def section(title):
    return [P(title, H2), HRFlowable(width="100%", thickness=1.4, color=colors.HexColor(BLUE),
                                     spaceBefore=0, spaceAfter=6)]


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


def data_table(header, rows, col_widths, trend_col=None, mono_cols=()):
    t = Table([header] + rows, colWidths=[c * mm for c in col_widths], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
             ("FONTNAME", (0, 0), (-1, 0), SANS_B), ("FONTNAME", (0, 1), (-1, -1), SANS),
             ("FONTSIZE", (0, 0), (-1, -1), 8.5),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(LIGHT)]),
             ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    for c in mono_cols:
        style.append(("FONTNAME", (c, 1), (c, -1), MONO))
    if trend_col is not None:
        for i, row in enumerate(rows, start=1):
            txt = str(row[trend_col])
            c = GREEN if txt.startswith("+") else RED if txt.startswith("-") else GREY
            style += [("TEXTCOLOR", (trend_col, i), (trend_col, i), colors.HexColor(c)),
                      ("FONTNAME", (trend_col, i), (trend_col, i), MONO_B)]
    t.setStyle(TableStyle(style))
    return t


def fmt_pct(p):
    return "n/a" if p is None else f"{p:+.2f}%"


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(SANS, 7.5)
    canvas.setFillColor(colors.HexColor(GREY))
    canvas.drawString(15 * mm, 8 * mm, f"Tech Radar - {TODAY_PRETTY} - Powered by {LLM_NAME}")
    canvas.drawRightString(195 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(path, weather, nse, glob, gh_data, news, reddit, brief, news_take):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=12 * mm, bottomMargin=16 * mm, title="Daily Tech Radar")
    s = [banner("Daily Tech Radar", f"{TODAY_PRETTY}  |  {LOCATION['name']}  |  Prepared for Steve"), Spacer(1, 8)]

    if brief:
        s += section("Today's brief") + [P(l.lstrip("-* ").strip(), BUL) for l in brief.splitlines() if l.strip()]

    # Weather: one compact card
    if weather:
        c, d = weather["current"], weather["daily"]
        s += [Spacer(1, 6), P(f"{LOCATION['name']} weather - today", H3),
              kpi_row([("Now", f"{c['temperature_2m']:.0f} C"),
                       ("High / Low", f"{d['temperature_2m_max'][0]:.0f} / {d['temperature_2m_min'][0]:.0f} C"),
                       ("Conditions", WMO.get(c["weather_code"], "-")),
                       ("Rain chance", f"{d['precipitation_probability_max'][0]}%"),
                       ("Wind", f"{c['wind_speed_10m']:.0f} km/h")])]

    # Markets
    s += section("Markets")
    if nse:
        s.append(P("NSE watchlist (KES, last close)", H3))
        rows = [[r["name"], r["sym"], "n/a" if r["price"] is None else f"{r['price']:.2f}", fmt_pct(r["pct"]),
                 "close" if r["fresh"] else ("stale" if r["price"] is not None else "not found")] for r in nse]
        s.append(data_table(["Company", "Ticker", "Price", "Change", "Data"], rows, [60, 25, 30, 30, 25],
                            trend_col=3, mono_cols=(2,)))
        ch = nse_chart(nse)
        if ch:
            s += [Spacer(1, 6), ch]
        missing = [r["sym"] for r in nse if r["price"] is None]
        note = "Prices are end-of-day closes (not live). Dangote Petroleum Refinery is unlisted, so no price."
        if missing:
            note += f" No NSE listing found for: {', '.join(missing)}."
        s.append(P(note, SMALL))
    if glob:
        s += [P("Global tech giants (USD)", H3),
              data_table(["Company", "Ticker", "Price", "1d", "30d"],
                         [[r["name"], r["sym"], f"${r['price']:.2f}", fmt_pct(r["pct"]), fmt_pct(r["pct30"])]
                          for r in glob],
                         [55, 25, 30, 30, 30], trend_col=3, mono_cols=(2, 4)), Spacer(1, 6), global_chart(glob)]

    # GitHub
    if gh_data:
        u = gh_data["user"]
        s += [PageBreak()] + section(f"GitHub - {GITHUB_USER}") + [
            kpi_row([("Commits (14d)", gh_data["total14"]), ("Public repos", u["public_repos"]),
                     ("Stars", gh_data["stars"]), ("Followers", u["followers"])]),
            Spacer(1, 6), github_charts(gh_data)]
        if gh_data["pushes"]:
            s += [P("Recent pushes", H3),
                  data_table(["When", "Repo", "#", "Latest message"],
                             [[w, r.split("/")[-1], n, latin(m)[:55]] for w, r, n, m in gh_data["pushes"]],
                             [28, 45, 10, 97], mono_cols=(0,))]

    # News
    s += [PageBreak()] + section("News & Intelligence")
    for sec, items in news.items():
        block = [P(sec, H3)]
        if news_take.get(sec):
            block += [P(l.lstrip("-* ").strip(), BUL) for l in news_take[sec].splitlines() if l.strip()]
        block.append(Spacer(1, 2))
        block += [Paragraph("&bull; " + link_markup(i["title"], i["url"]), SMALL) for i in items]
        s.append(KeepTogether(block))

    # Reddit
    s += [PageBreak()] + section("Reddit - what developers are discussing")
    rc = reddit_chart(reddit)
    if rc:
        s.append(rc)
    for lang, posts in reddit.items():
        if posts:
            s.append(P(lang, H3))
            for p in posts[:3]:
                s.append(Paragraph("&bull; " + link_markup(p["title"], p["url"]), SMALL))
    if not any(reddit.values()):
        s.append(P("Reddit blocked the request (common from cloud IPs). Try running locally.", SMALL))

    doc.build(s, onFirstPage=footer, onLaterPages=footer)


# ------------------------------------------------------------------ 6. EMAIL
def send_email(pdf_path, preview):
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not pw:
        print("GMAIL_APP_PASSWORD not set - PDF saved only.")
        return
    msg = MIMEMultipart()
    msg["Subject"] = f"Daily Tech Radar - {TODAY_PRETTY}"
    msg["From"], msg["To"] = SENDER_EMAIL, RECIPIENT_EMAIL
    html = f"""<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px">
      <h2 style="font-family:Georgia,serif;color:#0F172A;border-bottom:3px solid #2563EB;padding-bottom:6px">Daily Tech Radar</h2>
      <p style="color:#64748B">{escape(TODAY_PRETTY)}</p>
      <div style="color:#334155;line-height:1.6">{preview}</div>
      <p style="color:#94A3B8;font-size:12px">Full report with charts attached (PDF).</p></div>"""
    msg.attach(MIMEText(html, "html"))
    with open(pdf_path, "rb") as f:
        part = MIMEApplication(f.read(), _subtype="pdf")
    part.add_header("Content-Disposition", "attachment", filename=Path(pdf_path).name)
    msg.attach(part)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(SENDER_EMAIL, pw)
            srv.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print(f"Email sent with attachment {Path(pdf_path).name} ({Path(pdf_path).stat().st_size // 1024} KB).")
    except Exception as e:
        print(f"Email failed: {e}")


# ------------------------------------------------------------------ 7. ORCHESTRATOR
def run_agent():
    print(f"Agent started - {LLM_NAME}")
    weather = safe(get_weather, None, "weather")
    nse = safe(collect_nse, [], "nse")
    glob = safe(global_tech, [], "global tech")
    gh_data = safe(collect_github, None, "github")
    news = safe(collect_news, {}, "news")
    reddit = safe(collect_reddit, {}, "reddit")

    news_take = {sec: takeaways(sec, items) for sec, items in news.items()}

    movers = ", ".join(f"{r['sym']} {fmt_pct(r['pct'])}" for r in nse if r["pct"] is not None)
    context = (f"Weather: {weather['current']['temperature_2m']:.0f}C "
               f"{WMO.get(weather['current']['weather_code'], '')}\n" if weather else "")
    context += f"NSE movers: {movers or 'no history yet'}\n"
    if gh_data:
        context += f"GitHub: {gh_data['total14']} commits in 14 days\n"
    context += "\n".join(f"{k}: " + "; ".join(i["title"] for i in v[:3]) for k, v in news.items())
    brief = llm("Write a morning brief for a Nairobi software engineer: exactly 4 bullet lines ('- '), "
                "max 22 words each, most important first. No preamble.", context, 250)

    pdf_path = OUT_DIR / f"tech-radar-{TODAY}.pdf"
    build_pdf(pdf_path, weather, nse, glob, gh_data, news, reddit, brief, news_take)
    print(f"PDF built: {pdf_path}")

    preview = "<br>".join("&bull; " + escape(l.lstrip("-* ").strip()) for l in brief.splitlines() if l.strip()) \
        or "Your daily report is attached."
    send_email(pdf_path, preview)


if __name__ == "__main__":
    run_agent()