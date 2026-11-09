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
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, PageBreak, KeepTogether)

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

# NSE tickers (mystocks.co.ke codes). Dangote Petroleum Refinery is NOT listed
# on any exchange yet, so it cannot be priced. Add its ticker here if it lists.
NSE_WATCHLIST = {
    "Co-op Bank": "COOP",
    "Stanbic Holdings": "SBIC",
    "EABL": "EABL",
    "BK Group": "BKG",
    "Eveready (EGAD)": "EGAD",
    "Quickmart": "QMART",
}
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


# ------------------------------------------------------------------ THEME / CHART HELPERS
NAVY, BLUE, GREEN, RED, GREY, LIGHT = "#0F172A", "#2563EB", "#10B981", "#EF4444", "#64748B", "#F1F5F9"
PALETTE = [BLUE, GREEN, "#F59E0B", "#8B5CF6", "#EC4899", "#14B8A6", "#F97316"]

plt.rcParams.update({
    "font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#CBD5E1", "axes.labelcolor": GREY, "xtick.color": GREY,
    "ytick.color": GREY, "axes.titleweight": "bold", "axes.titlecolor": NAVY,
    "axes.grid": True, "grid.color": "#E2E8F0", "grid.linewidth": 0.6,
})


def fig_to_image(fig, width_mm=175) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=170, bbox_inches="tight", facecolor="white")
    w, h = fig.get_size_inches()
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=width_mm * mm, height=width_mm * mm * (h / w))


def latin(s: str) -> str:
    """Helvetica is latin-1 only; strip anything else so PDFs never show boxes."""
    return (s or "").encode("latin-1", "ignore").decode("latin-1")


def P(text, style):  # safe paragraph
    return Paragraph(escape(latin(text)), style)


def link_markup(title, url):
    return f'<a href="{escape(url)}" color="{BLUE}">{escape(latin(title))}</a>'


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
        "forecast_days": 7})
    r.raise_for_status()
    return r.json()


def weather_chart(w):
    d = w["daily"]
    days = [datetime.fromisoformat(x).strftime("%a %d") for x in d["time"]]
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.fill_between(days, d["temperature_2m_min"], d["temperature_2m_max"], color=BLUE, alpha=0.15)
    ax.plot(days, d["temperature_2m_max"], color=RED, marker="o", lw=2, label="High C")
    ax.plot(days, d["temperature_2m_min"], color=BLUE, marker="o", lw=2, label="Low C")
    ax.set_ylabel("Temperature (C)")
    ax2 = ax.twinx()
    ax2.bar(days, d["precipitation_sum"], color="#38BDF8", alpha=0.45, width=0.5, label="Rain mm")
    ax2.set_ylabel("Rain (mm)")
    ax2.grid(False)
    ax2.spines["right"].set_visible(True)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper center", ncol=3, frameon=False, fontsize=8)
    ax.set_title(f"{LOCATION['name']} - 7-day forecast", loc="left")
    return fig_to_image(fig)


# ------------------------------------------------------------------ 2. STOCKS
def get_nse_price(sym: str):
    """Scrapes mystocks.co.ke. Fragile by nature: verify the regex against the live page."""
    for url in (f"https://live.mystocks.co.ke/stock={sym}", f"https://live.mystocks.co.ke/m/stock={sym}"):
        try:
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if res.status_code == 200:
                m = re.search(r"KES\s*([\d,]+\.?\d*)", res.text)
                if m:
                    return float(m.group(1).replace(",", ""))
        except Exception:
            continue
    return None


def load_history():
    hist = defaultdict(dict)  # sym -> {date: price}
    if HISTORY_CSV.exists():
        with open(HISTORY_CSV, newline="") as f:
            for row in csv.DictReader(f):
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
    rows = []
    for name, sym in NSE_WATCHLIST.items():
        price = get_nse_price(sym)
        if price is not None:
            hist[sym][TODAY] = price
        series = sorted(hist[sym].items())
        prev = series[-2][1] if len(series) >= 2 else None
        cur = series[-1][1] if series else None
        pct = ((cur - prev) / prev * 100) if (cur and prev) else None
        rows.append({"name": name, "sym": sym, "price": cur, "pct": pct,
                     "fresh": price is not None, "series": series})
    save_history(hist)
    return rows


def nse_chart(rows):
    plotted = [r for r in rows if len(r["series"]) >= 2]
    if not plotted:
        return None
    fig, ax = plt.subplots(figsize=(8, 3.4))
    for i, r in enumerate(plotted):
        dates = [datetime.fromisoformat(d) for d, _ in r["series"]]
        base = r["series"][0][1]
        ax.plot(dates, [(p / base - 1) * 100 for _, p in r["series"]],
                lw=2, color=PALETTE[i % len(PALETTE)], label=r["sym"])
    ax.axhline(0, color=GREY, lw=0.8)
    ax.set_ylabel("Change since tracking began (%)")
    ax.set_title("NSE watchlist - relative performance", loc="left")
    ax.legend(ncol=6, frameon=False, fontsize=8, loc="upper center")
    fig.autofmt_xdate()
    return fig_to_image(fig)


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
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(8, 3.4))
    for i, r in enumerate(rows):
        s = r["series"]
        ax.plot(s.index, (s / s.iloc[0] - 1) * 100, lw=2, color=PALETTE[i % len(PALETTE)], label=r["sym"])
    ax.axhline(0, color=GREY, lw=0.8)
    ax.set_ylabel("30-day change (%)")
    ax.set_title("Big tech - 30-day performance", loc="left")
    ax.legend(ncol=6, frameon=False, fontsize=8, loc="upper center")
    fig.autofmt_xdate()
    return fig_to_image(fig)


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
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.2), gridspec_kw={"width_ratios": [2.2, 1]})
    vals = [g["per_day"].get(d, 0) for d in days]
    axes[0].bar([d.strftime("%d %b") for d in days], vals, color=GREEN)
    axes[0].set_title("Commits pushed - last 14 days", loc="left", fontsize=10)
    axes[0].tick_params(axis="x", rotation=60, labelsize=7)
    top = g["langs"].most_common(6)
    if top:
        axes[1].pie([c for _, c in top], labels=[l for l, _ in top], colors=PALETTE,
                    textprops={"fontsize": 7}, wedgeprops={"width": 0.45, "edgecolor": "white"})
        axes[1].set_title("Repo languages", fontsize=10)
    axes[1].grid(False)
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
    ax.set_title("Reddit buzz by language - today", loc="left")
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
H1 = ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=24,
                    textColor=colors.white, alignment=0, leading=28)
SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontSize=10, textColor=colors.HexColor("#CBD5E1"))
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=15,
                    textColor=colors.HexColor(NAVY), spaceBefore=10, spaceAfter=6)
H3 = ParagraphStyle("H3", parent=ss["Heading3"], fontSize=11, textColor=colors.HexColor(BLUE),
                    spaceBefore=8, spaceAfter=3)
BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontSize=9, leading=13, textColor=colors.HexColor("#334155"))
SMALL = ParagraphStyle("SMALL", parent=BODY, fontSize=8, textColor=colors.HexColor(GREY))
BUL = ParagraphStyle("BUL", parent=BODY, leftIndent=10, bulletIndent=0, spaceAfter=2)


def banner(title, subtitle):
    t = Table([[Paragraph(escape(title), H1)], [Paragraph(escape(subtitle), SUB)]], colWidths=[180 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(NAVY)),
                           ("LEFTPADDING", (0, 0), (-1, -1), 14), ("TOPPADDING", (0, 0), (0, 0), 16),
                           ("BOTTOMPADDING", (0, -1), (-1, -1), 14),
                           ("LINEBELOW", (0, -1), (-1, -1), 4, colors.HexColor(BLUE))]))
    return t


def kpi_row(items):
    cells = [[Paragraph(f'<font size="16" color="{NAVY}"><b>{escape(str(v))}</b></font><br/>'
                        f'<font size="7" color="{GREY}">{escape(k.upper())}</font>', BODY) for k, v in items]]
    t = Table(cells, colWidths=[180 * mm / len(items)] * len(items))
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(LIGHT)),
                           ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                           ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return t


def data_table(header, rows, col_widths, trend_col=None):
    t = Table([header] + rows, colWidths=[c * mm for c in col_widths], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(NAVY)), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
             ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(LIGHT)]),
             ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    if trend_col is not None:
        for i, row in enumerate(rows, start=1):
            txt = str(row[trend_col])
            c = GREEN if txt.startswith("+") else RED if txt.startswith("-") else GREY
            style += [("TEXTCOLOR", (trend_col, i), (trend_col, i), colors.HexColor(c)),
                      ("FONTNAME", (trend_col, i), (trend_col, i), "Helvetica-Bold")]
    t.setStyle(TableStyle(style))
    return t


def fmt_pct(p):
    return "n/a" if p is None else f"{p:+.2f}%"


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor(GREY))
    canvas.drawString(15 * mm, 8 * mm, f"Tech Radar - {TODAY_PRETTY} - Powered by {LLM_NAME}")
    canvas.drawRightString(195 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(path, weather, nse, glob, gh_data, news, reddit, brief, news_take):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=12 * mm, bottomMargin=16 * mm, title="Daily Tech Radar")
    s = [banner("Daily Tech Radar", f"{TODAY_PRETTY}  |  {LOCATION['name']}  |  Prepared for Steve"), Spacer(1, 8)]

    if brief:
        s += [P("Today's brief", H2)] + [P(l.lstrip("-* ").strip(), BUL) for l in brief.splitlines() if l.strip()]

    # Weather
    if weather:
        c, d = weather["current"], weather["daily"]
        s += [P("Weather - " + LOCATION["name"], H2),
              kpi_row([("Now", f"{c['temperature_2m']:.0f} C"), ("Conditions", WMO.get(c["weather_code"], "-")),
                       ("Humidity", f"{c['relative_humidity_2m']}%"), ("Wind", f"{c['wind_speed_10m']:.0f} km/h"),
                       ("Rain today", f"{d['precipitation_probability_max'][0]}%")]),
              Spacer(1, 6), weather_chart(weather)]

    # Markets
    s += [PageBreak(), P("Markets", H2), P("NSE watchlist (KES)", H3)]
    rows = [[r["name"], r["sym"], "n/a" if r["price"] is None else f"{r['price']:.2f}", fmt_pct(r["pct"]),
             "live" if r["fresh"] else "stale"] for r in nse]
    s.append(data_table(["Company", "Ticker", "Price", "Change", "Data"], rows, [60, 25, 30, 30, 25], trend_col=3))
    ch = nse_chart(nse)
    s += [Spacer(1, 6)] + ([ch] if ch else [P("Trend charts appear once 2+ days of history are stored.", SMALL)])
    s.append(P("Dangote Petroleum Refinery is unlisted, so no price is available.", SMALL))
    if glob:
        s += [P("Global tech giants (USD)", H3),
              data_table(["Company", "Ticker", "Price", "1d", "30d"],
                         [[r["name"], r["sym"], f"${r['price']:.2f}", fmt_pct(r["pct"]), fmt_pct(r["pct30"])]
                          for r in glob],
                         [55, 25, 30, 30, 30], trend_col=3), Spacer(1, 6), global_chart(glob)]

    # GitHub
    if gh_data:
        u = gh_data["user"]
        s += [PageBreak(), P(f"GitHub - {GITHUB_USER}", H2),
              kpi_row([("Commits (14d)", gh_data["total14"]), ("Public repos", u["public_repos"]),
                       ("Stars", gh_data["stars"]), ("Followers", u["followers"])]),
              Spacer(1, 6), github_charts(gh_data)]
        if gh_data["pushes"]:
            s += [P("Recent pushes", H3),
                  data_table(["When", "Repo", "#", "Latest message"],
                             [[w, r.split("/")[-1], n, latin(m)[:55]] for w, r, n, m in gh_data["pushes"]],
                             [28, 45, 10, 97])]

    # News
    s += [PageBreak(), P("News & Intelligence", H2)]
    for section, items in news.items():
        block = [P(section, H3)]
        if news_take.get(section):
            block += [P(l.lstrip("-* ").strip(), BUL) for l in news_take[section].splitlines() if l.strip()]
        block.append(Spacer(1, 2))
        block += [Paragraph("&bull; " + link_markup(i["title"], i["url"]), SMALL) for i in items]
        s.append(KeepTogether(block))

    # Reddit
    s += [PageBreak(), P("Reddit - what developers are discussing", H2)]
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
      <h2 style="color:#0F172A;border-bottom:3px solid #2563EB;padding-bottom:6px">Daily Tech Radar</h2>
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
        print("Email sent.")
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