"""TechWatch presentation layer.

Turns the data agent.py already collected into:
  * render_dashboard(ctx) -> self-contained HTML "intelligence terminal" (light/dark, interactive SVG charts)
  * render_email(ctx)     -> inline-styled HTML briefing for email clients
  * prepare(ctx)          -> shared prioritisation used by the dashboard, the email and the PDF

Rules this module follows:
  * It never fetches data and never invents values. Every number comes from ctx.
  * Sections with no connected data source say so explicitly instead of showing placeholders.
  * Facts, AI interpretation and community opinion are labelled differently.
"""
import json
import re
from datetime import datetime, timedelta
from html import escape as _esc

# ------------------------------------------------------------------ TOKENS
# News desks -> fixed categorical order (validated light + dark with the dataviz validator).
DESKS = ["Tech Giants", "DevOps & Cloud", "Cybersecurity", "Fintech", "Kenya Tech & Events"]
DESK_ICON = {"Tech Giants": "building-2", "DevOps & Cloud": "cloud", "Cybersecurity": "shield-alert",
             "Fintech": "landmark", "Kenya Tech & Events": "map-pin"}
# Same hexes, for the PDF and email (light surfaces only).
DESK_HEX = {"Tech Giants": "#2563EB", "DevOps & Cloud": "#0D9488", "Cybersecurity": "#7C3AED",
            "Fintech": "#D97706", "Kenya Tech & Events": "#DB2777"}

AI_RE = re.compile(r"\b(ai|a\.i\.|llms?|gpt[\w.-]*|openai|anthropic|claude|gemini|llama|copilot|chatbot|"
                   r"machine learning|deep learning|neural|agents?|agentic|nvidia|deepmind|mistral|model)\b", re.I)
DEV_RE = re.compile(r"\b(developers?|github|gitlab|open[- ]source|kubernetes|k8s|cloud|devops|apis?|sdk|rust|"
                    r"python|java|javascript|typescript|node(\.js)?|framework|database|linux|cncf|docker|"
                    r"programming|compiler|release[sd]?)\b", re.I)
FUNDING_RE = re.compile(r"\b(raises?|raised|funding|series [a-e]|seed round|pre-seed|acquir\w+|acquisition|"
                        r"ipo|valuation|invests?|investment|merger)\b", re.I)
HACK_RE = re.compile(r"hackathon|hack-a-thon|\bhack (night|day|week)|\bchallenge\b|competition|\bctf\b|"
                     r"pitch (night|day|competition)|accelerator|bootcamp|buildathon|ideathon|datathon", re.I)
# Stricter for headlines: "challenge" / "accelerator" are too common in ordinary news.
NEWS_HACK_RE = re.compile(r"hackathon|hack-a-thon|\bhack (night|day|week)|\bctf\b|coding competition|"
                          r"pitch competition|buildathon|ideathon|datathon", re.I)
MEETUP_RE = re.compile(r"meetup|meet-up|workshop|masterclass|training|lesson|session|clinic|class|"
                       r"study jam|community|summit|conference|forum", re.I)
TECH_RE = re.compile(r"tech|\bai\b|startup|developer|\bcode|coding|\bdata\b|cloud|cyber|fintech|software|"
                     r"product|hackathon|digital|web3|blockchain|robot|engineer|google|microsoft|aws|gdg|"
                     r"innovation|machine learning|founder|saas|devops|gaming|creative tech", re.I)

IMPACT_W = {"high": 40, "medium": 20, "low": 6}


def e(s):
    return _esc("" if s is None else str(s), quote=True)


def fmt_pct(p):
    return "n/a" if p is None else f"{p:+.2f}%"


def tone(p):
    if p is None:
        return "flat"
    return "up" if p > 0 else "down" if p < 0 else "flat"


def arrow(p):
    return {"up": "trending-up", "down": "trending-down"}.get(tone(p), "minus")


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def ago(dt, now):
    if not dt:
        return "time unknown"
    s = (now - dt).total_seconds()
    if s < 0:
        return dt.strftime("%d %b %H:%M")
    if s < 3600:
        return f"{max(1, int(s // 60))}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    if s < 7 * 86400:
        return f"{int(s // 86400)}d ago"
    return dt.strftime("%d %b %Y")


def until(dt, now):
    d = (dt.date() - now.date()).days
    return "Today" if d == 0 else "Tomorrow" if d == 1 else f"In {d} days" if d > 1 else "Started"


def lines(text):
    return [l.lstrip("-*• ").strip() for l in (text or "").splitlines() if l.strip().lstrip("-*• ")]


def fmt_num(v):
    if v is None:
        return "n/a"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.1f}{suf}"
    return f"{v:,.0f}"


# ------------------------------------------------------------------ PRIORITISATION
def prepare(ctx):
    """Score + flag news, classify events, derive opportunities. Pure function of ctx."""
    now = ctx["now"]
    kws = [k.lower() for k in ctx.get("keywords", ())]
    items = []
    for desk, entries in (ctx.get("news") or {}).items():
        for it in entries:
            it = dict(it)
            dt = parse_dt(it.get("published"))
            age_h = (now - dt).total_seconds() / 3600 if dt else None
            text = f"{it.get('title', '')} {it.get('summary', '')}"
            impact = (it.get("impact") or "").lower()
            score = IMPACT_W.get(impact, 12)
            score += max(0.0, 36 - age_h) if age_h is not None else 0
            stale = age_h is not None and age_h > 7 * 24
            if stale:                                                    # old aggregator results sink
                score -= 40
            kw_hit = [k for k in kws if re.search(rf"{re.escape(k)}", text, re.I)]
            score += 15 if kw_hit else 0
            score += 8 if desk == "Kenya Tech & Events" else 0          # geographic relevance
            flags = []
            if age_h is not None and age_h <= 3:
                flags.append("breaking")
            if not stale and (impact == "high" or score >= 70):
                flags.append("high")
            if FUNDING_RE.search(text):
                flags.append("funding")
            it.update(desk=desk, dt=dt, age_h=age_h, score=score, flags=flags, kw_hit=kw_hit,
                      is_ai=bool(AI_RE.search(text)), is_dev=bool(DEV_RE.search(text)) or desk == "DevOps & Cloud")
            items.append(it)
    items.sort(key=lambda x: -x["score"])

    events = []
    for ev in ctx.get("events") or []:
        ev = dict(ev)
        start = parse_dt(ev.get("start"))
        if not start or start < now - timedelta(hours=3):
            continue
        ev["dt"] = start
        name = ev.get("name", "")
        ev["online"] = ev.get("location_type") == "online"
        ev["is_hack"] = bool(HACK_RE.search(name))
        ev["is_meetup"] = bool(MEETUP_RE.search(name))
        ev["is_tech"] = bool(TECH_RE.search(name + " " + (ev.get("organizer") or "")))
        days = (start.date() - now.date()).days
        ev["days"] = days
        open_reg = ev.get("availability") in (None, "open")
        ev["register_now"] = (open_reg and not ev.get("sold_out")
                              and (days <= 3 or bool(ev.get("near_capacity"))))
        ev["score"] = (30 if ev["is_tech"] else 0) + (20 if ev["is_hack"] else 0) + max(0, 21 - days) \
            + (10 if ev.get("is_free") else 0) + (8 if not ev["online"] else 0)
        events.append(ev)
    events.sort(key=lambda x: x["dt"])

    news_mentions = [it for it in items if it["desk"] == "Kenya Tech & Events"]
    hack_news = [it for it in items if NEWS_HACK_RE.search(it.get("title", ""))]

    # Opportunities: deterministic, each one traceable to a collected item.
    th = ctx.get("move_threshold", 3)
    ops = []
    for ev in sorted(events, key=lambda x: -x["score"]):
        if ev["is_tech"] or ev["is_hack"]:
            ops.append({"kind": "Event", "icon": "calendar-check", "title": ev["name"],
                        "why": f"{until(ev['dt'], now)} · {'Free' if ev.get('is_free') else ev.get('price_text') or 'Paid'}"
                               f"{' · filling up' if ev.get('near_capacity') else ''} · {ev.get('organizer') or 'Luma'}",
                        "url": ev.get("url"), "source": "Luma"})
    for r in ctx.get("nse") or []:
        if r.get("pct") is not None and abs(r["pct"]) >= th:
            ops.append({"kind": "NSE mover", "icon": arrow(r["pct"]), "title": f"{r['sym']} moved {fmt_pct(r['pct'])}",
                        "why": f"{r['name']} at KES {r['price']:.2f}; above your {th:g}% alert threshold. Check for an announcement.",
                        "url": f"https://afx.kwayisi.org/nse/{r['sym'].lower()}.html", "source": "NSE close"})
    for r in ctx.get("glob") or []:
        day, month = abs(r["pct"]) >= th, abs(r.get("pct30", 0)) >= 12
        if day or month:
            why = " and ".join(x for x in (f"1-day move beyond your {th:g}% threshold" if day else "",
                                           "30-day move beyond 12%" if month else "") if x)
            ops.append({"kind": "Global mover", "icon": arrow(r["pct30"] if month and not day else r["pct"]),
                        "title": f"{r['sym']} {fmt_pct(r['pct'])} today, {fmt_pct(r['pct30'])} over 30 days",
                        "why": f"{why[0].upper()}{why[1:]}. Check the news flow before acting.",
                        "url": f"https://finance.yahoo.com/quote/{r['sym']}", "source": "Yahoo Finance"})
    for it in items:
        if "high" in it["flags"] or "funding" in it["flags"] or it["kw_hit"]:
            why = (f"AI note: {it['why']}" if it.get("why") else
                   "Matches your alert keywords: " + ", ".join(it["kw_hit"]) if it["kw_hit"]
                   else "Funding / M&A signal" if "funding" in it["flags"] else "Ranked high impact")
            ops.append({"kind": "Funding / M&A" if "funding" in it["flags"] else "Story", "icon": "newspaper",
                        "title": it["title"], "why": why, "url": it.get("url"), "source": it.get("source") or it["desk"]})
    return {"items": items, "events": events, "news_mentions": news_mentions,
            "hack_news": hack_news, "ops": ops[:9]}


# ------------------------------------------------------------------ SVG CHARTS
def spark(vals, tn, w=112, h=34):
    if len(vals) < 2:
        return '<span class="spark-empty">trend builds daily</span>'
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or (abs(hi) * 0.05) or 1
    pts = [(2 + i * (w - 6) / (len(vals) - 1), h - 4 - (v - lo) / span * (h - 8)) for i, v in enumerate(vals)]
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{d} L{pts[-1][0]:.1f},{h} L{pts[0][0]:.1f},{h} Z"
    return (f'<svg class="spark {tn}" width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-hidden="true">'
            f'<path class="area" d="{area}"/><path class="line" d="{d}"/>'
            f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2.6"/></svg>')


def price_chart(ohlc, sym, ccy="$"):
    """TradingView-style candles + volume panel, with a line-mode toggle and per-candle tooltips."""
    n = len(ohlc)
    if n < 2:
        return ""
    W, H, L, R, T, PH = 1080, 340, 6, 66, 10, 214
    VT, VH = PH + T + 18, 58
    lo = min(o[3] for o in ohlc)
    hi = max(o[2] for o in ohlc)
    pad = (hi - lo) * 0.08 or hi * 0.01
    lo, hi = lo - pad, hi + pad
    vmax = max((o[5] or 0) for o in ohlc) or 1

    def y(v):
        return T + (hi - v) / (hi - lo) * PH

    step = (W - L - R) / n
    bw = max(2.0, step * 0.62)
    out = [f'<svg class="chart mode-candle" viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="{e(sym)} daily price, last {n} sessions">']
    last_y = y(ohlc[-1][4])
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
        if abs(y(v) - last_y) > 14:                       # don't collide with the last-price tag
            out.append(f'<text class="axis" x="{W - R + 8}" y="{y(v) + 4:.1f}">{v:,.2f}</text>')
    out.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{VT + VH}" y2="{VT + VH}"/>'
               f'<text class="axis" x="{W - R + 8}" y="{VT + 10}">Vol</text>')
    closes = []
    for i, (d, o, h, l, c, v) in enumerate(ohlc):
        x = L + step * (i + 0.5)
        cls = "up" if c >= o else "down"
        closes.append((x, y(c)))
        top, bot = y(max(o, c)), y(min(o, c))
        out.append(f'<g class="candleview {cls}"><line x1="{x:.1f}" x2="{x:.1f}" y1="{y(h):.1f}" y2="{y(l):.1f}"/>'
                   f'<rect x="{x - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{max(1.2, bot - top):.1f}" rx="1"/></g>')
        vh = (v or 0) / vmax * VH
        out.append(f'<rect class="vol {cls}" x="{x - bw / 2:.1f}" y="{VT + VH - vh:.1f}" width="{bw:.1f}" '
                   f'height="{vh:.1f}" rx="1"/>')
    d = "M" + " L".join(f"{x:.1f},{yy:.1f}" for x, yy in closes)
    trend = "up" if ohlc[-1][4] >= ohlc[0][4] else "down"
    out.append(f'<g class="lineview {trend}"><path class="area" d="{d} L{closes[-1][0]:.1f},{T + PH} '
               f'L{closes[0][0]:.1f},{T + PH} Z"/><path class="line" d="{d}"/></g>')
    every = max(1, n // 6)
    for i in range(0, n, every):
        x = L + step * (i + 0.5)
        out.append(f'<text class="axis" x="{x:.1f}" y="{H - 2}" text-anchor="middle">'
                   f'{datetime.fromisoformat(ohlc[i][0]).strftime("%d %b")}</text>')
    last = ohlc[-1][4]
    ltone = "up" if last >= ohlc[-2][4] else "down"
    out.append(f'<rect class="lasttag {ltone}" x="{W - R + 2}" y="{y(last) - 9:.1f}" width="{R - 4}" height="18" rx="3"/>'
               f'<text class="lasttxt" x="{W - R + 8}" y="{y(last) + 4:.1f}">{last:,.2f}</text>')
    out.append(f'<line class="xhair" x1="0" x2="0" y1="{T}" y2="{VT + VH}"/>')
    for i, (dd, o, h, l, c, v) in enumerate(ohlc):
        x = L + step * (i + 0.5)
        chg = (c / ohlc[i - 1][4] - 1) * 100 if i else None
        tip = (f"<b>{e(sym)}</b> · {datetime.fromisoformat(dd).strftime('%a %d %b %Y')}<br>"
               f"O {o:,.2f} &nbsp;H {h:,.2f}<br>L {l:,.2f} &nbsp;C <b>{c:,.2f}</b><br>"
               f"Change {fmt_pct(chg)} · Vol {fmt_num(v)}")
        out.append(f'<rect class="hit" x="{x - step / 2:.1f}" y="{T}" width="{step:.1f}" height="{VT + VH - T}" '
                   f'data-x="{x:.1f}" data-tip="{e(tip)}"/>')
    out.append("</svg>")
    return "".join(out)


def diverging_bars(rows, ccy="KES"):
    """rows: [(sym, name, pct, price)] -> horizontal bars from a zero line; values in a right column."""
    if not rows:
        return ""
    W, rh, L, R = 560, 26, 70, 70
    H = len(rows) * rh + 6
    m = max(abs(r[2]) for r in rows) or 1
    mid = L + (W - L - R) / 2
    half = (W - L - R) / 2 - 4
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Top NSE gainers and losers">',
           f'<line class="zero" x1="{mid}" x2="{mid}" y1="0" y2="{H}"/>']
    for i, (sym, name, p, price) in enumerate(rows):
        yy = 3 + i * rh
        bw = max(2, abs(p) / m * half)
        x = mid if p >= 0 else mid - bw
        cls = "up" if p > 0 else "down" if p < 0 else "flat"
        tip = f"<b>{e(sym)}</b> {e(name)}<br>{ccy} {price:,.2f} · {fmt_pct(p)}"
        out.append(f'<g data-tip="{e(tip)}"><rect class="hit" x="0" y="{yy}" width="{W}" height="{rh}"/>'
                   f'<text class="lbl" x="{L - 10}" y="{yy + rh / 2 + 4}" text-anchor="end">{e(sym)}</text>'
                   f'<rect class="bar {cls}" x="{x:.1f}" y="{yy + 5}" width="{bw:.1f}" height="{rh - 10}" rx="4"/>'
                   f'<text class="val" x="{W - 4}" y="{yy + rh / 2 + 4}" text-anchor="end">{fmt_pct(p)}</text></g>')
    out.append("</svg>")
    return "".join(out)


def commit_bars(per_day, now):
    days = [(now - timedelta(days=i)).date() for i in range(13, -1, -1)]
    vals = [per_day.get(d, 0) for d in days]
    peak = max(vals) or 1
    W, H, B, T = 560, 150, 22, 16
    step = W / len(days)
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Commits per day, last 14 days">',
           f'<line class="grid" x1="0" x2="{W}" y1="{H - B}" y2="{H - B}"/>']
    for i, (d, v) in enumerate(zip(days, vals)):
        x = i * step + step * 0.2
        bh = v / peak * (H - B - T)
        cls = "bar seq-strong" if v == peak and v else "bar seq"
        if v:
            out.append(f'<rect class="{cls}" x="{x:.1f}" y="{H - B - bh:.1f}" width="{step * 0.6:.1f}" '
                       f'height="{bh:.1f}" rx="3"/>')
        if v == peak and v:
            out.append(f'<text class="val" x="{x + step * 0.3:.1f}" y="{H - B - bh - 4:.1f}" text-anchor="middle">{v}</text>')
        if i % 2 == 1 or i == len(days) - 1:
            out.append(f'<text class="axis" x="{x + step * 0.3:.1f}" y="{H - 6}" text-anchor="middle">{d.strftime("%d %b")}</text>')
        tip = f"<b>{d.strftime('%a %d %b')}</b><br>{v} commit(s)"
        out.append(f'<rect class="hit" x="{i * step:.1f}" y="0" width="{step:.1f}" height="{H}" data-tip="{e(tip)}"/>')
    out.append("</svg>")
    return "".join(out)


def range_bar(lo, hi, cur):
    if lo is None or hi is None or hi <= lo:
        return ""
    pct = (cur - lo) / (hi - lo) * 100
    return (f'<div class="range" title="30-day range {lo:,.2f} – {hi:,.2f}"><span style="left:{pct:.0f}%"></span></div>'
            f'<div class="range-lbl"><span>{lo:,.2f}</span><span>{hi:,.2f}</span></div>')


# ------------------------------------------------------------------ COMPONENTS
def icon(name, cls=""):
    return f'<i data-lucide="{name}" class="ic {cls}" aria-hidden="true"></i>'


BADGE = {
    "breaking": ("zap", "Breaking", "Published within the last 3 hours"),
    "high": ("flame", "High priority", "Ranked by impact, recency and your keywords"),
    "funding": ("banknote", "Funding / M&A", "Mentions funding, acquisition or investment"),
    "upcoming": ("calendar-clock", "Upcoming", "Starts within 7 days"),
    "closing": ("alarm-clock", "Register soon", "Starts within 3 days or close to capacity"),
    "verify": ("circle-help", "Needs verification", "Not an official or structured source"),
    "verified": ("badge-check", "Market data", "Prices from a market data source"),
    "ai": ("sparkles", "AI analysis", "Model-generated interpretation, not a fact"),
    "community": ("users", "Community opinion", "Reddit posts are opinions, not facts"),
    "free": ("ticket", "Free", "Listed as free on the event page"),
    "paid": ("ticket", "Paid", "Listed as paid on the event page"),
    "online": ("monitor", "Online", ""),
    "inperson": ("map-pin", "In person", ""),
    "hack": ("trophy", "Hackathon", ""),
    "meetup": ("users-round", "Meetup / workshop", ""),
    "kw": ("bell-ring", "Your keyword", "Matches ALERT_KEYWORDS"),
    "stale": ("history", "Stale", "Source did not return a price this run; showing last stored close"),
    "offline": ("plug-zap", "Not connected", "No collector exists for this source yet"),
}


def badge(kind, label=None):
    ic, text, title = BADGE[kind]
    return f'<span class="badge b-{kind}" title="{e(title)}">{icon(ic)}{e(label or text)}</span>'


def desk_tag(desk):
    idx = DESKS.index(desk) if desk in DESKS else -1
    return (f'<span class="tag" style="--cat:var(--c{idx})">{icon(DESK_ICON.get(desk, "newspaper"))}'
            f'{e(desk)}</span>') if idx >= 0 else f'<span class="tag">{e(desk)}</span>'


def section(num, sid, ic, title, sub, body, accent="var(--blue)"):
    return (f'<section id="{sid}" class="sec" style="--accent:{accent}">'
            f'<header class="sec-h"><div class="sec-ic">{icon(ic)}</div><div>'
            f'<div class="sec-num">{num:02d}</div><h2>{e(title)}</h2><p class="sec-sub">{sub}</p></div></header>'
            f'{body}</section>')


def provenance(text):
    return f'<p class="prov">{icon("database")}{text}</p>'


def empty(msg):
    return f'<div class="empty">{icon("inbox")}<span>{msg}</span></div>'


def not_connected(title, shows, needs, links=()):
    lk = "".join(f'<a href="{e(u)}" target="_blank" rel="noopener">{e(t)} {icon("external-link")}</a>'
                 for t, u in links)
    return (f'<div class="nc"><div class="nc-h">{badge("offline")}<h3>{e(title)}</h3></div>'
            f'<p><b>Will show:</b> {shows}</p><p><b>Needed:</b> {needs}</p>'
            f'{f"<div class=nc-links>{lk}</div>" if lk else ""}</div>')


def news_card(it, now, featured=False):
    flags = "".join(badge(f) for f in it["flags"]) + (badge("kw") if it["kw_hit"] else "")
    summ = it.get("summary") or ""
    why = it.get("why")
    impact = (it.get("impact") or "").lower()
    meter = ""
    if impact in IMPACT_W:
        lvl = {"low": 1, "medium": 2, "high": 3}[impact]
        meter = (f'<span class="impact i{lvl}" title="AI-estimated impact">Impact '
                 + "".join(f'<i class="{"on" if k < lvl else ""}"></i>' for k in range(3)) + f' {impact}</span>')
    pub = it["dt"].strftime("%d %b %Y, %H:%M EAT") if it["dt"] else "date not provided"
    summ_html = f'<p class="summary">{e(summ)}</p>' if summ else ""
    why_html = f'<div class="why">{badge("ai")}<p><b>Why it matters:</b> {e(why)}</p></div>' if why else ""
    return (f'<article class="news{" featured" if featured else ""}" style="--cat:var(--c{DESKS.index(it["desk"]) if it["desk"] in DESKS else 0})">'
            f'<div class="news-top">{desk_tag(it["desk"])}{flags}</div>'
            f'<h3><a href="{e(it.get("url"))}" target="_blank" rel="noopener">{e(it["title"])}</a></h3>'
            f'<div class="meta">{icon("rss")}<b>{e(it.get("source") or it["desk"])}</b>'
            f'<span title="{e(pub)}">{icon("clock")}{e(ago(it["dt"], now))}</span>{meter}</div>'
            f'{summ_html}{why_html}'
            f'<a class="src" href="{e(it.get("url"))}" target="_blank" rel="noopener">Read original {icon("arrow-up-right")}</a>'
            f'</article>')


def event_card(ev, now):
    kinds = ["online" if ev["online"] else "inperson"]
    if ev["is_hack"]:
        kinds.append("hack")
    if ev["is_meetup"]:
        kinds.append("meetup")
    if ev["register_now"]:
        kinds.append("closing")
    badges = "".join(badge(k) for k in kinds)
    if ev.get("is_free") is True:
        badges += badge("free")
    elif ev.get("is_free") is False:
        badges += badge("paid", ev.get("price_text") or "Paid")
    if 0 <= ev["days"] <= 7 and not ev["register_now"]:
        badges += badge("upcoming")
    filt = " ".join(["all"] + [k for k in kinds] + (["tech"] if ev["is_tech"] else []))
    end = parse_dt(ev.get("end"))
    when = ev["dt"].strftime("%a %d %b · %H:%M") + (f"–{end.strftime('%H:%M')}" if end and end.date() == ev["dt"].date() else "")
    where = "Online" if ev["online"] else ", ".join(x for x in (ev.get("area"), ev.get("city")) if x) or "Location on request"
    reg = []
    if ev.get("sold_out"):
        reg.append("Sold out")
    elif ev.get("availability"):
        reg.append(f"Registration {ev['availability']}")
    if ev.get("approval"):
        reg.append("host approval required")
    if ev.get("near_capacity"):
        reg.append("near capacity")
    if ev.get("spots") is not None:
        reg.append(f"{ev['spots']} spots left")
    signals = []
    if ev.get("guests"):
        signals.append(f"{ev['guests']} registered")
    if ev["is_tech"]:
        signals.append("tech-relevant title/host")
    if not ev["online"]:
        signals.append("in Nairobi")
    sig_html = (f'<p class="ev-why">{icon("sparkles")}<span><b>Signals:</b> {e(" · ".join(signals))}</span></p>'
                if signals else "")
    cover = (f'<div class="ev-cover" style="background-image:url(\'{e(ev["cover"])}\')"></div>' if ev.get("cover")
             else f'<div class="ev-cover none">{icon("calendar-days")}</div>')
    return (f'<article class="event" data-kinds="{filt}">{cover}'
            f'<div class="ev-date"><b>{ev["dt"].strftime("%d")}</b><span>{ev["dt"].strftime("%b")}</span></div>'
            f'<div class="ev-body"><div class="badges">{badges}</div>'
            f'<h3><a href="{e(ev.get("url"))}" target="_blank" rel="noopener">{e(ev["name"])}</a></h3>'
            f'<ul class="ev-facts"><li>{icon("building")}{e(ev.get("organizer") or "Organizer not listed")}</li>'
            f'<li>{icon("clock")}{e(when)} <em>({until(ev["dt"], now)})</em></li>'
            f'<li>{icon("map-pin")}{e(where)}</li>'
            f'<li>{icon("clipboard-check")}{e(", ".join(reg) or "Registration details on event page")}</li></ul>'
            f'{sig_html}'
            f'<a class="btn" href="{e(ev.get("url"))}" target="_blank" rel="noopener">{icon("ticket")}Register on {e(ev.get("source", "Luma"))}</a>'
            f'</div></article>')


def mini_list(items, now, n=6):
    if not items:
        return ""
    rows = "".join(f'<li><a href="{e(i.get("url"))}" target="_blank" rel="noopener">{e(i["title"])}</a>'
                   f'<span>{e(i.get("source") or i["desk"])} · {e(ago(i["dt"], now))}</span></li>' for i in items[:n])
    return f'<ul class="mini">{rows}</ul>'


# ------------------------------------------------------------------ SECTIONS
def s_exec(ctx, P):
    now = ctx["now"]
    brief = lines(ctx.get("brief"))
    bl = "".join(f"<li>{e(l)}</li>" for l in brief) or "<li>The AI briefing model was unavailable this run. Key data is summarised in the tiles.</li>"
    tiles = []
    nse = [r for r in ctx.get("nse") or [] if r.get("pct") is not None]
    if nse:
        b = max(nse, key=lambda r: r["pct"])
        w = min(nse, key=lambda r: r["pct"])
        tiles.append(("Best NSE watchlist", b["sym"], fmt_pct(b["pct"]), tone(b["pct"]), "landmark"))
        tiles.append(("Weakest NSE watchlist", w["sym"], fmt_pct(w["pct"]), tone(w["pct"]), "landmark"))
    mk = ctx.get("nse_market") or []
    if mk:
        adv = sum(1 for r in mk if (r.get("pct") or 0) > 0)
        dec = sum(1 for r in mk if (r.get("pct") or 0) < 0)
        tiles.append(("NSE breadth", f"{adv}▲ {dec}▼", f"{len(mk)} counters priced", "up" if adv > dec else "down" if dec > adv else "flat", "activity"))
    gl = ctx.get("glob") or []
    if gl:
        avg = sum(r["pct"] for r in gl) / len(gl)
        tiles.append(("Big tech avg (1D)", fmt_pct(avg), f"{len(gl)} stocks", tone(avg), "cpu"))
    hp = sum(1 for i in P["items"] if "high" in i["flags"])
    tiles.append(("High-priority stories", str(hp), f"of {len(P['items'])} collected", "flat", "flame"))
    wk = [ev for ev in P["events"] if ev["days"] <= 7]
    tiles.append(("Events this week", str(len(wk)), f"{sum(1 for ev in P['events'] if ev['register_now'])} need registration now", "flat", "calendar-days"))
    tl = "".join(f'<div class="tile {t}"><div class="tile-l">{icon(ic)}{e(lbl)}</div><div class="tile-v">{e(v)}</div>'
                 f'<div class="tile-s">{e(s)}</div></div>' for lbl, v, s, t, ic in tiles)
    gen = now.strftime("%H:%M EAT, %d %b %Y")
    body = (f'<div class="hero"><div class="hero-l"><div class="hero-k">{icon("radar")}Executive briefing · {e(ctx["now"].strftime("%A"))}</div>'
            f'<ol class="brief">{bl}</ol>'
            f'<div class="hero-foot">{badge("ai")}<span>Synthesised by {e(ctx.get("llm_name"))} from the data in this report. Generated {e(gen)}.</span></div></div>'
            f'<div class="tiles">{tl}</div></div>'
            f'<div class="legend"><b>Indicators</b>{"".join(badge(k) for k in ("breaking", "high", "closing", "upcoming", "verify", "ai", "community"))}'
            f'<span class="badge b-up">{icon("trending-up")}Positive</span><span class="badge b-down">{icon("trending-down")}Negative</span></div>')
    return section(1, "brief", "sparkles", "Today's Executive Briefing",
                   "What matters most today, distilled from every source below.", body, "var(--purple)")


def s_top_news(ctx, P):
    now = ctx["now"]
    top = P["items"][:7]
    if not top:
        return section(2, "news", "newspaper", "Top Technology News", "", empty("No news feeds responded this run."))
    feat = news_card(top[0], now, True)
    rest = "".join(news_card(i, now) for i in top[1:])
    takes = ctx.get("news_take") or {}
    desks = []
    for desk in DESKS + [d for d in (ctx.get("news") or {}) if d not in DESKS]:
        its = [i for i in P["items"] if i["desk"] == desk]
        if not its:
            continue
        tk = "".join(f"<li>{e(l)}</li>" for l in lines(takes.get(desk)))
        desks.append(f'<div class="desk" style="--cat:var(--c{DESKS.index(desk) if desk in DESKS else 0})">'
                     f'<div class="desk-h">{desk_tag(desk)}<span class="count">{len(its)} stories</span></div>'
                     f'{f"<ul class=takes>{tk}</ul>" if tk else ""}{mini_list(its, now, 6)}</div>')
    body = (f'<div class="news-grid">{feat}{rest}</div>'
            f'<h3 class="sub-h">{icon("layout-grid")}Coverage by desk {badge("ai", "AI takeaways")}</h3>'
            f'<div class="desk-grid">{"".join(desks)}</div>'
            + provenance(f"RSS feeds: {', '.join(ctx.get('feed_sources') or [])}. Ranked by AI-estimated impact, recency, "
                         "your alert keywords and Kenyan relevance. Summaries are the publishers' own text."))
    return section(2, "news", "newspaper", "Top Technology News",
                   "Ranked, not chronological. Breaking = published in the last 3 hours.", body, "var(--blue)")


def s_ai_dev(ctx, P):
    now = ctx["now"]
    shown = {i["url"] for i in P["items"][:7]}
    ai = [i for i in P["items"] if i["is_ai"] and i["url"] not in shown][:4]
    dev = [i for i in P["items"] if i["is_dev"] and not i["is_ai"] and i["url"] not in shown][:4]
    cols = (f'<div class="two"><div><h3 class="sub-h">{icon("brain-circuit")}AI developments</h3>'
            f'{"".join(news_card(i, now) for i in ai) or empty("No additional AI stories beyond the top news.")}</div>'
            f'<div><h3 class="sub-h">{icon("code-xml")}Developer ecosystem</h3>'
            f'{"".join(news_card(i, now) for i in dev) or empty("No additional developer-ecosystem stories.")}</div></div>')
    g = ctx.get("gh")
    ghb = ""
    if g:
        u = g["user"]
        kp = "".join(f'<div class="kpi"><span>{e(k)}</span><b>{e(v)}</b></div>' for k, v in (
            ("Commits · 14d", g["total14"]), ("Public repos", u.get("public_repos")),
            ("Stars", g["stars"]), ("Followers", u.get("followers"))))
        langs = g["langs"].most_common(6)
        tot = sum(c for _, c in langs) or 1
        lb = "".join(f'<div class="lang"><span>{e(l)}</span><div class="lbar"><i style="width:{c / tot * 100:.0f}%"></i></div><b>{c}</b></div>'
                     for l, c in langs)
        push = "".join(f'<tr><td class="mono">{e(w)}</td><td>{e(r.split("/")[-1])}</td><td class="num">{n}</td><td>{e(m)}</td></tr>'
                       for w, r, n, m in g["pushes"])
        ghb = (f'<h3 class="sub-h">{icon("git-branch")}Your GitHub · {e(ctx.get("github_user"))}</h3>'
               f'<div class="card"><div class="kpis">{kp}</div><div class="two tight">'
               f'<div><div class="chart-t">Commits pushed, last 14 days</div>{commit_bars(g["per_day"], now)}</div>'
               f'<div><div class="chart-t">Repository languages (count of repos)</div>{lb}</div></div>'
               f'{f"<div class=tbl-wrap><table><thead><tr><th>When</th><th>Repo</th><th class=num>Commits</th><th>Latest message</th></tr></thead><tbody>{push}</tbody></table></div>" if push else ""}'
               + provenance("GitHub REST API · your events, repos and commit history") + '</div>')
    return section(3, "aidev", "cpu", "AI and Developer Updates",
                   "Model releases, AI business moves, tooling and the open-source ecosystem.", cols + ghb, "var(--teal)")


def s_events(ctx, P):
    now = ctx["now"]
    evs = [ev for ev in P["events"] if not ev["online"]]
    chips = "".join(f'<button class="chip{" on" if k == "all" else ""}" data-filter="{k}">{icon(ic)}{e(t)}</button>'
                    for k, t, ic in (("all", "All", "layers"), ("tech", "Tech-relevant", "cpu"),
                                     ("inperson", "In person", "map-pin"), ("hack", "Hackathons", "trophy"),
                                     ("meetup", "Meetups & workshops", "users-round"),
                                     ("closing", "Register now", "alarm-clock")))
    cards = "".join(event_card(ev, now) for ev in sorted(evs, key=lambda x: (-x["register_now"], x["dt"])))
    grid = (f'<div class="chips" role="toolbar" aria-label="Filter events">{chips}</div>'
            f'<div class="ev-grid" data-filterable>{cards}</div>' if cards else
            empty("Luma returned no upcoming Nairobi events this run."))
    mentions = P["news_mentions"][:6]
    mm = (f'<h3 class="sub-h">{icon("megaphone")}Event mentions in Kenyan tech news {badge("verify")}</h3>'
          f'<p class="note">Headlines that mention summits, launches or hackathons. Dates and registration are not extracted. Open the article to confirm.</p>'
          f'{mini_list(mentions, now, 6)}') if mentions else ""
    nc = not_connected("Instagram, WhatsApp channels, LinkedIn, Microsoft Events, university & community calendars",
                       "More Nairobi, Kiambu and Thika events with banners, deadlines and registration links.",
                       "These platforms have no public feed. They need an API token, a feed you subscribe to, "
                       "or forwarding posts to the agent. Until then only Luma is covered.")
    body = grid + provenance(f"Luma public discover feed for Nairobi · {len(evs)} upcoming in-person events · "
                             "free/paid and capacity as published by the host") + mm + nc
    return section(4, "events", "map-pinned", "Nairobi Technology Events",
                   "In-person events near you. Events starting within 3 days or filling up are pinned first.", body, "var(--pink)")


def s_hacks(ctx, P):
    now = ctx["now"]
    hev = [ev for ev in P["events"] if ev["is_hack"]]
    body = "".join(event_card(ev, now) for ev in hev)
    body = f'<div class="ev-grid">{body}</div>' if body else empty("No hackathons or competitions listed on Luma Nairobi right now.")
    if P["hack_news"]:
        body += (f'<h3 class="sub-h">{icon("newspaper")}Hackathons & challenges in the news {badge("verify")}</h3>'
                 + mini_list(P["hack_news"], now, 6))
    body += not_connected("Devpost, MLH, Lablab.ai, Zindi, Kaggle",
                          "Open competitions with prize pools, submission deadlines and online/in-person format.",
                          "A collector for each platform's public listings (Devpost and MLH publish season pages, "
                          "Zindi has a public competitions API).",
                          (("Zindi", "https://zindi.africa/competitions"), ("Devpost", "https://devpost.com/hackathons"),
                           ("MLH", "https://mlh.io/seasons/2027/events")))
    return section(5, "hacks", "trophy", "Upcoming Hackathons", "Competitions, challenges and build weekends.", body, "var(--amber)")


def s_global_events(ctx, P):
    now = ctx["now"]
    online = [ev for ev in P["events"] if ev["online"]]
    body = f'<div class="ev-grid">{"".join(event_card(ev, now) for ev in online)}</div>' if online else ""
    body += not_connected("Global & online tech events",
                          "Major conferences (developer keynotes, AI summits), webinars and online workshops.",
                          "An online-events source such as Luma's AI/tech discover categories or conference calendars. "
                          "Online events from the Nairobi Luma feed appear above when present.")
    return section(6, "global", "globe", "Global Technology Events", "Online and international events worth your time.", body, "var(--teal)")


def s_stocks(ctx, P):
    gl = ctx.get("glob") or []
    if not gl:
        return section(7, "stocks", "candlestick-chart", "Stock Market Performance", "",
                       empty("Global market data (yfinance) was unavailable this run."))
    best = max(gl, key=lambda r: r["pct"])
    worst = min(gl, key=lambda r: r["pct"])
    asof = max(r["ohlc"][-1][0] for r in gl if r.get("ohlc")) if any(r.get("ohlc") for r in gl) else ""
    tabs = "".join(f'<button class="tab{" on" if i == 0 else ""}" data-tab="px-{e(r["sym"])}">'
                   f'<b>{e(r["sym"])}</b><span class="{tone(r["pct"])}">{fmt_pct(r["pct"])}</span></button>'
                   for i, r in enumerate(gl))
    panes = "".join(f'<div class="pane{" on" if i == 0 else ""}" id="px-{e(r["sym"])}">'
                    f'<div class="pane-h"><div><span class="tick">{e(r["sym"])}</span> {e(r["name"])}</div>'
                    f'<div class="px">${r["price"]:,.2f} <span class="{tone(r["pct"])}">{icon(arrow(r["pct"]))}{fmt_pct(r["pct"])}</span></div></div>'
                    f'{price_chart(r.get("ohlc") or [], r["sym"])}</div>' for i, r in enumerate(gl))
    rows = ""
    for r in sorted(gl, key=lambda x: -x["pct"]):
        o = r.get("ohlc") or []
        lo = min((x[3] for x in o), default=None)
        hi = max((x[2] for x in o), default=None)
        vol = o[-1][5] if o else None
        rows += (f'<tr><td><span class="tick">{e(r["sym"])}</span> <span class="muted">{e(r["name"])}</span></td>'
                 f'<td class="num mono">${r["price"]:,.2f}</td>'
                 f'<td class="num"><span class="chg {tone(r["pct"])}">{fmt_pct(r["pct"])}</span></td>'
                 f'<td class="num"><span class="chg {tone(r["pct30"])}">{fmt_pct(r["pct30"])}</span></td>'
                 f'<td class="num mono">{fmt_num(vol)}</td><td class="rng">{range_bar(lo, hi, r["price"])}</td>'
                 f'<td>{spark([x[4] for x in o], tone(r["pct30"]))}</td></tr>')
    take = lines(ctx.get("market_take"))
    commentary = ("".join(f"<li>{e(l)}</li>" for l in take) if take else
                  "<li>AI commentary unavailable this run. Use the facts panel.</li>")
    facts = (f'<ul class="facts"><li>{icon("trophy")}Best: <b>{e(best["sym"])}</b> <span class="chg {tone(best["pct"])}">{fmt_pct(best["pct"])}</span></li>'
             f'<li>{icon("triangle-alert")}Worst: <b>{e(worst["sym"])}</b> <span class="chg {tone(worst["pct"])}">{fmt_pct(worst["pct"])}</span></li>'
             f'<li>{icon("activity")}Up today: <b>{sum(1 for r in gl if r["pct"] > 0)}</b> of {len(gl)}</li>'
             f'<li>{icon("calendar")}Last session: <b>{e(asof)}</b></li></ul>')
    body = (f'<div class="term"><div class="tabs" role="tablist">{tabs}</div>{panes}'
            f'<div class="term-foot"><span class="tg"><button class="seg on" data-mode="candle">{icon("chart-candlestick")}Candles</button>'
            f'<button class="seg" data-mode="line">{icon("chart-spline")}Line</button></span>'
            f'<span>Daily OHLC · volume panel below · hover for values</span></div></div>'
            f'<div class="two analysis"><div class="card">{badge("verified")}<h3>Verified facts</h3>{facts}</div>'
            f'<div class="card">{badge("ai")}<h3>Daily market analysis</h3><ul class="takes">{commentary}</ul>'
            f'<p class="note">Interpretation only. Where headlines do not explain a move, the cause is unknown. Not investment advice.</p></div></div>'
            f'<div class="tbl-wrap"><table><thead><tr><th>Stock</th><th class="num">Last</th><th class="num">1D</th>'
            f'<th class="num">30D</th><th class="num">Volume</th><th>30D range</th><th>Trend</th></tr></thead><tbody>{rows}</tbody></table></div>'
            + provenance(f"Yahoo Finance via yfinance · daily bars, last close {e(asof)} (US market, delayed)"))
    return section(7, "stocks", "candlestick-chart", "Stock Market Performance",
                   "Your global tech watchlist. Market data and AI commentary are kept apart.", body, "var(--blue)")


def s_nse(ctx, P):
    nse = ctx.get("nse") or []
    mk = ctx.get("nse_market") or []
    rows = ""
    for r in nse:
        if r["price"] is None:
            rows += (f'<tr><td><span class="tick">{e(r["sym"])}</span> <span class="muted">{e(r["name"])}</span></td>'
                     f'<td colspan="4" class="muted">Not found on the NSE listing</td></tr>')
            continue
        st = "" if r["fresh"] else badge("stale")
        rows += (f'<tr><td><span class="tick">{e(r["sym"])}</span> <span class="muted">{e(r["name"])}</span></td>'
                 f'<td class="num mono">KES {r["price"]:,.2f} {st}</td>'
                 f'<td class="num"><span class="chg {tone(r["pct"])}">{icon(arrow(r["pct"]))}{fmt_pct(r["pct"])}</span></td>'
                 f'<td class="num mono">{fmt_num(r.get("volume"))}</td>'
                 f'<td>{spark([p for _, p in r["series"]], tone(r["pct"]))}</td></tr>')
    watch = (f'<div class="tbl-wrap"><table><thead><tr><th>Watchlist</th><th class="num">Last close</th>'
             f'<th class="num">Change</th><th class="num">Volume</th><th>History</th></tr></thead><tbody>{rows}</tbody></table></div>'
             if rows else empty("NSE watchlist is empty."))
    breadth = movers = ""
    if mk:
        priced = [r for r in mk if r.get("pct") is not None]
        adv = sum(1 for r in priced if r["pct"] > 0)
        dec = sum(1 for r in priced if r["pct"] < 0)
        unch = len(mk) - adv - dec
        tot = len(mk) or 1
        breadth = (f'<div class="card"><h3>{icon("activity")}Market breadth · {len(mk)} counters</h3>'
                   f'<div class="breadth"><i class="up" style="flex:{adv}" title="{adv} advancing"></i>'
                   f'<i class="flat" style="flex:{unch}" title="{unch} unchanged / no trade"></i>'
                   f'<i class="down" style="flex:{dec}" title="{dec} declining"></i></div>'
                   f'<div class="breadth-l"><span><b class="up-t">{adv}</b> advancing ({adv / tot * 100:.0f}%)</span>'
                   f'<span><b>{unch}</b> unchanged or no trade</span><span><b class="down-t">{dec}</b> declining</span></div></div>')
        g = sorted([r for r in priced if r["pct"] > 0], key=lambda r: -r["pct"])[:5]
        l = sorted([r for r in priced if r["pct"] < 0], key=lambda r: r["pct"])[:5][::-1]
        sel = [(r["sym"], r.get("name") or "", r["pct"], r["price"]) for r in g + l]
        movers = (f'<div class="card"><h3>{icon("bar-chart-horizontal")}Top gainers & losers, whole NSE</h3>'
                  f'{diverging_bars(sel) if sel else empty("No price changes reported for the last session.")}</div>')
    src = ctx.get("nse_source") or "NSE end-of-day table"
    body = (watch + f'<div class="two">{breadth}{movers}</div>'
            + provenance(f"{e(src)} · end-of-day closes, not live · watchlist history stored in data/nse_history.csv "
                         "since 10 Nov 2026, so sparklines grow daily"))
    return section(8, "nse", "landmark", "Kenyan NSE Market Intelligence",
                   "Your watchlist, plus breadth and movers across every listed counter.", body, "var(--emerald)")


def s_dividends(ctx, P):
    cols = "".join(f"<th>{c}</th>" for c in ("Company", "Ticker", "DPS (KES)", "Type", "Book closure",
                                              "Ex-date", "Payment", "Yield", "Source"))
    body = (f'<div class="tbl-wrap ghost"><table><thead><tr>{cols}</tr></thead><tbody>'
            f'<tr><td colspan="9" class="muted center">{icon("plug-zap")} No dividend data collected. '
            f'This calendar fills only from official announcements and is never estimated.</td></tr></tbody></table></div>'
            + not_connected("NSE corporate announcements",
                            "Dividends (interim/final, DPS), book closure, ex-dividend, register and payment dates, "
                            "rights and bonus issues, splits and results releases, in a calendar and table. Yield is shown only "
                            "when the DPS and current price are both verified.",
                            "A collector for NSE's official corporate-announcements listings (PDF notices), "
                            "with every row linked to its source notice. Unconfirmed items would be flagged.",
                            (("NSE website", "https://www.nse.co.ke/"), ("Capital Markets Authority", "https://www.cma.or.ke/"))))
    return section(9, "dividends", "calendar-range", "Dividend & Corporate Action Calendar",
                   "Official corporate actions only. Amounts and dates are never inferred.", body, "var(--amber)")


def s_mmf(ctx, P):
    funds = ctx.get("mmf_watch") or []
    chips = "".join(f'<span class="fund">{icon("piggy-bank")}{e(f)}<em>no data yet</em></span>' for f in funds)
    explain = ('<div class="card explain"><h3>' + icon("graduation-cap") + 'Reading MMF yields</h3><ul>'
               '<li><b>Published yields are historical.</b> An "effective annual yield" is what the fund earned recently, '
               'annualised. It is not a promised return.</li>'
               '<li><b>Yields track short-term rates.</b> When Treasury bill rates and the CBK rate fall, MMF yields usually '
               'drift down weeks later. When they rise, yields follow.</li>'
               '<li><b>Compare net of fees.</b> Check whether a quoted yield is before or after the management fee, and '
               'whether withholding tax (15% on interest) has been deducted.</li>'
               '<li><b>A sharp yield jump needs a reason.</b> It can mean riskier holdings or a one-off gain. Read the fund '
               'fact sheet before moving money.</li></ul></div>')
    body = (f'<div class="funds">{chips}</div>' + f'<div class="two">{explain}'
            + not_connected("Kenyan MMF yields",
                            "Latest published annualised yields, history chart, fees, minimum investment, withdrawal "
                            "terms and fund manager for each fund, side by side.",
                            "A collector for each fund manager's published daily/weekly yields, or the CMA quarterly "
                            "CIS report. Yields would be stored daily in data/ so trends build over time.",
                            (("CMA – Collective Investment Schemes", "https://www.cma.or.ke/"),)) + '</div>')
    return section(10, "mmf", "piggy-bank", "MMF & Investment Updates",
                   "Past yields do not guarantee future returns. This section never shows a yield it could not source.",
                   body, "var(--teal)")


def s_reddit(ctx, P):
    rd = ctx.get("reddit") or {}
    subs = ctx.get("reddit_subs") or {}
    cards = ""
    for label, posts in rd.items():
        if not posts:
            continue
        sub = subs.get(label, label)
        lis = "".join(f'<li><a href="{e(p["url"])}" target="_blank" rel="noopener">{e(p["title"])}</a>'
                      f'<span>{icon("message-square")}{e(p["comments"]) if p.get("comments") is not None else "engagement n/a"}</span></li>'
                      for p in posts[:5])
        cards += (f'<div class="card rd"><div class="rd-h"><span class="sub">r/{e(sub)}</span><span class="muted">{e(label)}</span>'
                  f'<a href="https://www.reddit.com/r/{e(sub)}/top/?t=day" target="_blank" rel="noopener">{icon("external-link")}</a></div>'
                  f'<ul class="mini">{lis}</ul></div>')
    body = (f'<div class="banner-note">{badge("community")}<span>Reddit posts are opinions. Nothing here is verified financial '
            f'or technical fact. Market data lives in sections 7 and 8.</span></div>'
            + (f'<div class="rd-grid">{cards}</div>' if cards else empty("Reddit blocked the request this run (common from cloud IPs)."))
            + not_connected("Investor subreddits, bull/bear analysis, engagement counts",
                            "Investing and Kenyan finance threads, main arguments split into bullish and bearish, upvotes and comment counts.",
                            "Reddit's RSS gives titles only. Arguments and engagement need the Reddit API (free OAuth app) "
                            "plus an AI pass to summarise both sides of each thread.")
            + provenance("Reddit top-of-day RSS · cached copy used when Reddit blocks the request"))
    return section(11, "reddit", "messages-square", "Reddit & Community Sentiment",
                   "What developers and investors are discussing.", body, "var(--orange)")


def s_ops(ctx, P):
    ops = P["ops"]
    if not ops:
        body = empty("Nothing crossed your thresholds today.")
    else:
        body = '<div class="ops">' + "".join(
            f'<a class="op" href="{e(o["url"])}" target="_blank" rel="noopener"><div class="op-ic">{icon(o["icon"])}</div>'
            f'<div><span class="op-k">{e(o["kind"])}</span><h3>{e(o["title"])}</h3><p>{e(o["why"])}</p>'
            f'<span class="op-s">{icon("link")}{e(o["source"])}</span></div></a>' for o in ops) + "</div>"
    body += provenance("Built by rules from this report's data: tech events, moves ≥ your alert threshold, "
                       "high-impact and funding stories, keyword matches. Each card links to its source.")
    return section(12, "ops", "lightbulb", "Opportunities Worth Watching",
                   "Leads to look into, not recommendations.", body, "var(--purple)")


def s_status(ctx):
    rows = "".join(
        f'<li class="{"ok" if s["ok"] else "bad" if s["ok"] is False else "off"}">'
        f'{icon("circle-check" if s["ok"] else "circle-x" if s["ok"] is False else "plug-zap")}'
        f'<b>{e(s["name"])}</b><span>{e(s["detail"])}</span></li>' for s in ctx.get("status") or [])
    return (f'<section class="status" id="sources"><h2>{icon("server")}Data sources this run</h2>'
            f'<ul>{rows}</ul></section>')


NAV = [("brief", "sparkles", "Briefing"), ("news", "newspaper", "Top news"), ("aidev", "cpu", "AI & dev"),
       ("events", "map-pinned", "Nairobi events"), ("hacks", "trophy", "Hackathons"), ("global", "globe", "Global events"),
       ("stocks", "candlestick-chart", "Stocks"), ("nse", "landmark", "NSE"), ("dividends", "calendar-range", "Dividends"),
       ("mmf", "piggy-bank", "MMFs"), ("reddit", "messages-square", "Community"), ("ops", "lightbulb", "Opportunities"),
       ("sources", "server", "Sources")]


def ticker_tape(ctx):
    cells = []
    for r in ctx.get("nse") or []:
        if r.get("price") is not None:
            cells.append(f'<span><b>{e(r["sym"])}</b> {r["price"]:,.2f} <em class="{tone(r["pct"])}">{fmt_pct(r["pct"])}</em></span>')
    for r in ctx.get("glob") or []:
        cells.append(f'<span><b>{e(r["sym"])}</b> {r["price"]:,.2f} <em class="{tone(r["pct"])}">{fmt_pct(r["pct"])}</em></span>')
    if not cells:
        return ""
    run = "".join(cells)
    return f'<div class="tape" aria-label="Watchlist prices"><div class="tape-run">{run}<span aria-hidden="true" class="dup">{run}</span></div></div>'


def weather_chip(ctx):
    w = ctx.get("weather")
    if not w:
        return ""
    c, d = w["current"], w["daily"]
    cond = (ctx.get("wmo") or {}).get(c["weather_code"], "")
    return (f'<span class="wx" title="Open-Meteo · {e(ctx["location"])}">{icon("cloud-sun")}'
            f'{c["temperature_2m"]:.0f}°C {e(cond)} · H{d["temperature_2m_max"][0]:.0f}° L{d["temperature_2m_min"][0]:.0f}° · '
            f'{d["precipitation_probability_max"][0]}% rain</span>')


# ------------------------------------------------------------------ PAGE
def render_dashboard(ctx):
    P = prepare(ctx)
    now = ctx["now"]
    secs = [s_exec(ctx, P), s_top_news(ctx, P), s_ai_dev(ctx, P), s_events(ctx, P), s_hacks(ctx, P),
            s_global_events(ctx, P), s_stocks(ctx, P), s_nse(ctx, P), s_dividends(ctx, P), s_mmf(ctx, P),
            s_reddit(ctx, P), s_ops(ctx, P), s_status(ctx)]
    nav = "".join(f'<a href="#{sid}" data-nav="{sid}">{icon(ic)}<span>{e(t)}</span></a>' for sid, ic, t in NAV)
    return f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TechWatch Terminal · {e(now.strftime('%d %b %Y'))}</title>
<meta name="color-scheme" content="light dark">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
<script>(function(){{var t=localStorage.getItem('tw-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');document.documentElement.dataset.theme=t;}})();</script>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#brief">Skip to briefing</a>
<header class="top">
  <div class="brand"><span class="logo">{icon("radar")}</span><div><b>TechWatch</b><span>Personal intelligence terminal</span></div></div>
  <div class="top-mid"><span class="live"><i></i>{e(now.strftime('%a %d %b %Y · %H:%M EAT'))}</span>{weather_chip(ctx)}</div>
  <button class="theme" id="theme" aria-label="Toggle light or dark theme">{icon("moon", "i-dark")}{icon("sun", "i-light")}</button>
</header>
{ticker_tape(ctx)}
<div class="shell">
  <nav class="side" aria-label="Sections">{nav}</nav>
  <main>{''.join(secs)}
  <footer class="foot">TechWatch · generated {e(now.strftime('%d %b %Y %H:%M EAT'))} · AI by {e(ctx.get('llm_name'))} ·
  Prices are delayed or end-of-day. Nothing here is investment advice.</footer></main>
</div>
<div id="tip" role="tooltip" hidden></div>
<script src="https://unpkg.com/lucide@latest"></script>
<script>{JS}</script>
</body></html>"""


# ------------------------------------------------------------------ EMAIL (inline styles only)
def render_email(ctx):
    P = prepare(ctx)
    now = ctx["now"]
    up, down, mut, ink = "#15803D", "#DC2626", "#64748B", "#0F172A"

    def col(p):
        return up if (p or 0) > 0 else down if (p or 0) < 0 else mut

    def h(t, c="#3B82F6"):
        return (f'<tr><td style="padding:22px 24px 6px"><div style="font:700 11px Segoe UI,Arial;letter-spacing:.12em;'
                f'text-transform:uppercase;color:{c}">{e(t)}</div></td></tr>')

    brief = "".join(f'<li style="margin:0 0 8px">{e(l)}</li>' for l in lines(ctx.get("brief"))) or "<li>See the attached report.</li>"
    stories = ""
    for it in P["items"][:6]:
        c = DESK_HEX.get(it["desk"], "#3B82F6")
        fl = " · ".join(t for t, k in (("BREAKING", "breaking"), ("HIGH PRIORITY", "high"), ("FUNDING/M&amp;A", "funding")) if k in it["flags"])
        fl_html = f' · <span style="color:{down}">{fl}</span>' if fl else ""
        why_html = (f'<div style="font:13px/1.5 Segoe UI,Arial;color:#475569;margin-top:4px"><b>Why it matters:</b> '
                    f'{e(it["why"])}</div>') if it.get("why") else ""
        stories += (f'<tr><td style="padding:10px 24px;border-top:1px solid #EEF2F7">'
                    f'<div style="font:600 11px Segoe UI,Arial;color:{mut}"><span style="display:inline-block;width:8px;height:8px;'
                    f'border-radius:50%;background:{c};margin-right:6px"></span>{e(it["desk"])} · {e(it.get("source") or "")} · '
                    f'{e(ago(it["dt"], now))}{fl_html}</div>'
                    f'<a href="{e(it.get("url"))}" style="font:600 15px Segoe UI,Arial;color:{ink};text-decoration:none;line-height:1.35">{e(it["title"])}</a>'
                    f'{why_html}'
                    f'</td></tr>')
    mk = ""
    for r in (ctx.get("nse") or []) + (ctx.get("glob") or []):
        if r.get("price") is None:
            continue
        cur = "KES " if r in (ctx.get("nse") or []) else "$"
        mk += (f'<tr><td style="padding:6px 0;font:600 13px Consolas,monospace;color:{ink}">{e(r["sym"])}</td>'
               f'<td style="padding:6px 0;font:13px Consolas,monospace;color:{ink};text-align:right">{cur}{r["price"]:,.2f}</td>'
               f'<td style="padding:6px 0 6px 14px;font:700 13px Consolas,monospace;color:{col(r["pct"])};text-align:right">{fmt_pct(r["pct"])}</td></tr>')
    evs = ""
    for ev in sorted(P["events"], key=lambda x: (-x["register_now"], x["dt"]))[:4]:
        tag = f'<span style="color:{down};font-weight:700"> · REGISTER SOON</span>' if ev["register_now"] else ""
        evs += (f'<tr><td style="padding:8px 24px;border-top:1px solid #EEF2F7"><div style="font:600 11px Segoe UI,Arial;color:{mut}">'
                f'{e(ev["dt"].strftime("%a %d %b · %H:%M"))} · {"Free" if ev.get("is_free") else e(ev.get("price_text") or "Paid")}{tag}</div>'
                f'<a href="{e(ev.get("url"))}" style="font:600 14px Segoe UI,Arial;color:{ink};text-decoration:none">{e(ev["name"])}</a>'
                f'<div style="font:12px Segoe UI,Arial;color:{mut}">{e(ev.get("organizer") or "")}</div></td></tr>')
    ops = "".join(f'<li style="margin:0 0 6px"><b>{e(o["kind"])}:</b> <a href="{e(o["url"])}" style="color:#2563EB">{e(o["title"])}</a></li>'
                  for o in P["ops"][:5])
    return f"""<div style="background:#F1F5F9;padding:20px 0"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"
 style="max-width:640px;margin:0 auto;background:#FFFFFF;border-radius:14px;overflow:hidden;border:1px solid #E2E8F0">
<tr><td style="background:#101827;background:linear-gradient(135deg,#101827,#172554);padding:26px 24px;border-bottom:4px solid #3B82F6">
<div style="font:700 11px Segoe UI,Arial;letter-spacing:.16em;color:#93C5FD;text-transform:uppercase">TechWatch · Daily intelligence</div>
<div style="font:800 26px Segoe UI,Arial;color:#FFFFFF;margin-top:6px">{e(now.strftime('%A, %d %B'))}</div>
<div style="font:13px Segoe UI,Arial;color:#CBD5E1;margin-top:4px">Generated {e(now.strftime('%H:%M EAT'))} · {len(P['items'])} stories · {len(P['events'])} Nairobi events</div></td></tr>
{h("Executive briefing", "#8B5CF6")}
<tr><td style="padding:4px 24px 0"><ol style="margin:0;padding-left:18px;font:14px/1.55 Segoe UI,Arial;color:#1E293B">{brief}</ol>
<div style="font:11px Segoe UI,Arial;color:{mut};margin-top:4px">AI summary of collected data · {e(ctx.get('llm_name'))}</div></td></tr>
{h("Top stories")}{stories}
{h("Markets · last close", "#16A34A") if mk else ""}
{f'<tr><td style="padding:0 24px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0">{mk}</table><div style="font:11px Segoe UI,Arial;color:{mut}">NSE end-of-day · US via Yahoo Finance (delayed)</div></td></tr>' if mk else ""}
{h("Nairobi events", "#DB2777") if evs else ""}{evs}
{h("Worth watching", "#F59E0B") if ops else ""}
{f'<tr><td style="padding:0 24px"><ul style="margin:0;padding-left:18px;font:13px/1.5 Segoe UI,Arial;color:#1E293B">{ops}</ul></td></tr>' if ops else ""}
<tr><td style="padding:22px 24px;font:12px/1.5 Segoe UI,Arial;color:{mut};border-top:1px solid #EEF2F7;margin-top:16px">
Attached: the <b>interactive dashboard</b> (open the .html in a browser) and the PDF report. Not investment advice.</td></tr>
</table></div>"""


# ------------------------------------------------------------------ CSS / JS
CSS = r"""
:root{--navy:#101827;--midnight:#172554;--blue:#3B82F6;--teal:#14B8A6;--purple:#8B5CF6;--emerald:#16A34A;--red:#EF4444;
--amber:#F59E0B;--pink:#EC4899;--orange:#F97316;
--bg:#F8FAFC;--surface:#FFFFFF;--surface-2:#F1F5F9;--border:#E2E8F0;--ink:#0F172A;--ink-2:#334155;--muted:#64748B;
--up:#16A34A;--up-ink:#15803D;--down:#EF4444;--down-ink:#DC2626;--flat:#94A3B8;
--c0:#2563EB;--c1:#0D9488;--c2:#7C3AED;--c3:#D97706;--c4:#DB2777;
--shadow:0 1px 2px rgba(15,23,42,.04),0 4px 16px rgba(15,23,42,.06);--shadow-h:0 2px 4px rgba(15,23,42,.06),0 12px 28px rgba(15,23,42,.1);
--r:14px;--font:Inter,ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif;--mono:"JetBrains Mono",ui-monospace,Consolas,monospace}
[data-theme=dark]{--bg:#0B1120;--surface:#101827;--surface-2:#162033;--border:#1F2A40;--ink:#E5E9F0;--ink-2:#CBD5E1;--muted:#94A3B8;
--up:#22C55E;--up-ink:#4ADE80;--down:#F87171;--down-ink:#F87171;--flat:#64748B;
--c0:#3B82F6;--c1:#0D9488;--c2:#8B5CF6;--c3:#D97706;--c4:#EC4899;
--shadow:0 1px 2px rgba(0,0,0,.3);--shadow-h:0 10px 30px rgba(0,0,0,.45)}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:120px}
body{margin:0;overflow-x:hidden;background:var(--bg);color:var(--ink);font:15px/1.55 var(--font);-webkit-font-smoothing:antialiased}
a{color:inherit}.skip{position:absolute;left:-999px}.skip:focus{left:8px;top:8px;z-index:99;background:var(--surface);padding:8px}
.ic{width:16px;height:16px;flex:none;stroke-width:2}
:focus-visible{outline:2px solid var(--blue);outline-offset:2px;border-radius:6px}
.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:16px;padding:10px 22px;background:var(--navy);
color:#E2E8F0;border-bottom:1px solid #1E293B}
.brand{display:flex;align-items:center;gap:10px}.brand b{display:block;font-size:16px;letter-spacing:.02em;color:#fff}
.brand span{font-size:11px;color:#94A3B8;text-transform:uppercase;letter-spacing:.12em}
.logo{display:grid;place-items:center;width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,var(--blue),var(--purple))}
.logo .ic{width:20px;height:20px;color:#fff}
.top-mid{flex:1;display:flex;gap:18px;justify-content:center;flex-wrap:wrap;font-size:13px;color:#CBD5E1}
.live,.wx{display:inline-flex;align-items:center;gap:7px}.live i{width:8px;height:8px;border-radius:50%;background:#22C55E;
box-shadow:0 0 0 3px rgba(34,197,94,.25);animation:pulse 2s infinite}
@keyframes pulse{50%{box-shadow:0 0 0 6px rgba(34,197,94,0)}}
.theme{background:#1E293B;border:1px solid #334155;color:#E2E8F0;border-radius:10px;width:38px;height:38px;cursor:pointer;display:grid;place-items:center}
[data-theme=dark] .i-dark,[data-theme=light] .i-light{display:none}
.tape{overflow:hidden;background:#0B1222;border-bottom:1px solid #1E293B;font:500 12.5px var(--mono);color:#CBD5E1;position:sticky;top:57px;z-index:29}
.tape-run{display:inline-flex;white-space:nowrap;animation:tape 60s linear infinite}.tape:hover .tape-run{animation-play-state:paused}
.tape span{display:inline-flex;gap:8px;padding:7px 18px;border-right:1px solid #1E293B}.tape b{color:#fff}
.tape .dup{padding:0;border:0;gap:0}
.tape em{font-style:normal}.tape em.up{color:#4ADE80}.tape em.down{color:#F87171}.tape em.flat{color:#94A3B8}
@keyframes tape{to{transform:translateX(-50%)}}
.shell{display:grid;grid-template-columns:220px minmax(0,1fr);gap:28px;max-width:1440px;margin:0 auto;padding:24px 22px 60px}
.side{position:sticky;top:120px;align-self:start;display:flex;flex-direction:column;gap:2px}
.side a{display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:9px;text-decoration:none;color:var(--muted);font-weight:500;font-size:14px}
.side a:hover{background:var(--surface-2);color:var(--ink)}.side a.on{background:var(--surface);color:var(--ink);box-shadow:var(--shadow)}
.side a.on .ic{color:var(--blue)}
main{min-width:0}
.sec{margin-bottom:44px}.sec-h{display:flex;gap:14px;align-items:flex-start;margin-bottom:18px;padding-bottom:14px;border-bottom:1px solid var(--border)}
.sec-ic{display:grid;place-items:center;width:42px;height:42px;border-radius:12px;background:color-mix(in srgb,var(--accent) 14%,transparent);color:var(--accent);flex:none}
.sec-ic .ic{width:22px;height:22px}
.sec-num{font:600 11px var(--mono);color:var(--accent);letter-spacing:.1em}
.sec h2{margin:0;font-size:24px;line-height:1.2;letter-spacing:-.02em;font-weight:800}
.sec-sub{margin:4px 0 0;color:var(--muted);font-size:14px}
.sub-h{display:flex;align-items:center;gap:8px;font-size:15px;margin:26px 0 12px;color:var(--ink-2)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:18px;box-shadow:var(--shadow)}
.card h3{display:flex;align-items:center;gap:8px;margin:6px 0 12px;font-size:15px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}.two.tight{gap:24px;margin-top:10px}
.analysis{margin:18px 0}.tbl-wrap+.two{margin-top:18px}
.hero{display:grid;grid-template-columns:1.25fr 1fr;gap:18px;padding:24px;border-radius:18px;color:#E2E8F0;
background:radial-gradient(1200px 400px at 0% 0%,rgba(59,130,246,.35),transparent 60%),radial-gradient(800px 300px at 100% 100%,rgba(139,92,246,.35),transparent 60%),linear-gradient(135deg,var(--navy),var(--midnight));
box-shadow:var(--shadow-h);position:relative;overflow:hidden}
.hero:before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);background-size:28px 28px;pointer-events:none}
.hero>*{position:relative}
.hero-k{display:flex;align-items:center;gap:8px;font:600 12px var(--font);letter-spacing:.14em;text-transform:uppercase;color:#93C5FD}
.brief{margin:14px 0;padding-left:22px;font-size:16px;line-height:1.55;color:#F1F5F9}.brief li{margin-bottom:10px;padding-left:4px}
.brief li::marker{color:#93C5FD;font-weight:700}
.hero-foot{display:flex;gap:10px;align-items:center;font-size:12px;color:#94A3B8;flex-wrap:wrap}
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:10px;align-content:start}
.tile{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:12px 14px;backdrop-filter:blur(6px)}
.tile-l{display:flex;gap:6px;align-items:center;font-size:11.5px;color:#94A3B8;text-transform:uppercase;letter-spacing:.06em}
.tile-l .ic{width:14px;height:14px}
.tile-v{font:700 22px var(--mono);color:#fff;margin-top:4px}.tile-s{font-size:12px;color:#CBD5E1}
.tile.up .tile-s{color:#4ADE80}.tile.down .tile-s{color:#F87171}
.legend{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:14px;font-size:12px;color:var(--muted)}
.legend b{margin-right:4px}
.badge{display:inline-flex;align-items:center;gap:5px;font:600 11.5px var(--font);padding:3px 9px;border-radius:999px;white-space:nowrap;
background:var(--surface-2);color:var(--ink-2);border:1px solid var(--border)}
.badge .ic{width:13px;height:13px}
.b-breaking{background:color-mix(in srgb,var(--red) 14%,transparent);color:var(--down-ink);border-color:transparent}
.b-high,.b-closing{background:color-mix(in srgb,var(--amber) 18%,transparent);color:#B45309;border-color:transparent}
[data-theme=dark] .b-high,[data-theme=dark] .b-closing{color:#FBBF24}
.b-closing{background:color-mix(in srgb,var(--red) 14%,transparent);color:var(--down-ink)}
.b-upcoming,.b-inperson{background:color-mix(in srgb,var(--blue) 14%,transparent);color:#1D4ED8;border-color:transparent}
[data-theme=dark] .b-upcoming,[data-theme=dark] .b-inperson{color:#93C5FD}
.b-verify,.b-community{background:color-mix(in srgb,var(--orange) 14%,transparent);color:#C2410C;border-color:transparent}
[data-theme=dark] .b-verify,[data-theme=dark] .b-community{color:#FDBA74}
.b-ai{background:color-mix(in srgb,var(--purple) 16%,transparent);color:#6D28D9;border-color:transparent}
[data-theme=dark] .b-ai{color:#C4B5FD}
.hero .b-ai{color:#DDD6FE;background:rgba(139,92,246,.25)}
.b-verified,.b-free,.b-up{background:color-mix(in srgb,var(--emerald) 14%,transparent);color:var(--up-ink);border-color:transparent}
.b-down{background:color-mix(in srgb,var(--red) 14%,transparent);color:var(--down-ink);border-color:transparent}
.b-hack,.b-funding{background:color-mix(in srgb,var(--amber) 16%,transparent);color:#B45309;border-color:transparent}
[data-theme=dark] .b-hack,[data-theme=dark] .b-funding{color:#FCD34D}
.b-kw{background:color-mix(in srgb,var(--teal) 16%,transparent);color:#0F766E;border-color:transparent}
[data-theme=dark] .b-kw{color:#5EEAD4}
.b-offline{background:var(--surface-2);color:var(--muted);border-style:dashed}
.tag{display:inline-flex;align-items:center;gap:6px;font:600 12px var(--font);padding:3px 10px 3px 8px;border-radius:999px;color:var(--ink-2);
background:color-mix(in srgb,var(--cat,var(--muted)) 13%,transparent)}
.tag .ic{width:13px;height:13px;color:var(--cat)}
.news-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.news{position:relative;display:flex;flex-direction:column;gap:8px;background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
padding:16px 18px 14px;box-shadow:var(--shadow);transition:transform .18s,box-shadow .18s;border-top:3px solid var(--cat)}
.news:hover{transform:translateY(-2px);box-shadow:var(--shadow-h)}
.news.featured{grid-column:span 2;padding:22px 24px;
background:linear-gradient(160deg,color-mix(in srgb,var(--cat) 9%,var(--surface)),var(--surface) 55%)}
.news.featured h3{font-size:24px;line-height:1.25}.news.featured .summary{font-size:15.5px;-webkit-line-clamp:6}
.news-top{display:flex;flex-wrap:wrap;gap:6px}
.news h3{margin:2px 0 0;font-size:16px;line-height:1.35;letter-spacing:-.01em}.news h3 a{text-decoration:none}.news h3 a:hover{color:var(--blue)}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;font-size:12.5px;color:var(--muted)}
.meta>span,.meta{gap:6px}.meta .ic{width:13px;height:13px}.meta>span{display:inline-flex;align-items:center;gap:5px}
.summary{margin:0;color:var(--ink-2);font-size:14px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.why{display:flex;flex-direction:column;gap:6px;align-items:flex-start;background:var(--surface-2);border-radius:10px;padding:10px 12px}
.why p{margin:0;font-size:13.5px;color:var(--ink-2)}
.impact{display:inline-flex;align-items:center;gap:3px;text-transform:capitalize}.impact i{width:6px;height:10px;border-radius:2px;background:var(--border)}
.impact.i3 i.on{background:var(--red)}.impact.i2 i.on{background:var(--amber)}.impact.i1 i.on{background:var(--flat)}
.src{margin-top:auto;display:inline-flex;align-items:center;gap:4px;font-size:13px;font-weight:600;color:var(--blue);text-decoration:none}
.src .ic{width:14px;height:14px}
.desk-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.desk{background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--cat);border-radius:var(--r);padding:16px;box-shadow:var(--shadow)}
.desk-h{display:flex;justify-content:space-between;align-items:center}.count{font-size:12px;color:var(--muted)}
.takes{margin:10px 0;padding-left:18px;font-size:13.5px;color:var(--ink-2)}.takes li{margin-bottom:5px}
.mini{list-style:none;margin:8px 0 0;padding:0}.mini li{padding:8px 0;border-top:1px solid var(--border);font-size:13.5px}
.mini li:first-child{border-top:0}
.mini a{text-decoration:none;font-weight:500;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.mini a:hover{color:var(--blue)}
.mini span{display:inline-flex;gap:5px;align-items:center;font-size:12px;color:var(--muted)}.mini .ic{width:12px;height:12px}
.two>div>.news{margin-bottom:14px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.kpi{background:var(--surface-2);border-radius:10px;padding:10px 12px}.kpi span{font-size:12px;color:var(--muted);display:block}
.kpi b{font:700 20px var(--mono)}
.chart-t{font-size:12.5px;font-weight:600;color:var(--muted);margin-bottom:6px}
.lang{display:grid;grid-template-columns:96px 1fr 28px;gap:8px;align-items:center;font-size:13px;margin:7px 0}
.lbar{height:8px;background:var(--surface-2);border-radius:99px;overflow:hidden}.lbar i{display:block;height:100%;background:var(--teal);border-radius:99px}
.lang b{font:600 12px var(--mono);text-align:right}
.tbl-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:var(--r);background:var(--surface);margin-top:16px;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);background:var(--surface-2);padding:10px 14px;white-space:nowrap}
td{padding:10px 14px;border-top:1px solid var(--border);vertical-align:middle}
tbody tr:hover{background:color-mix(in srgb,var(--blue) 5%,transparent)}
.num{text-align:right}.mono{font-family:var(--mono)}.muted{color:var(--muted)}.center{text-align:center;padding:26px}
.tick{font:700 13px var(--mono);background:var(--surface-2);padding:2px 7px;border-radius:6px}
.chg{display:inline-flex;align-items:center;gap:4px;font:700 13px var(--mono);padding:2px 8px;border-radius:6px}
.chg .ic{width:14px;height:14px}
.chg.up{color:var(--up-ink);background:color-mix(in srgb,var(--up) 12%,transparent)}
.chg.down{color:var(--down-ink);background:color-mix(in srgb,var(--down) 12%,transparent)}.chg.flat{color:var(--muted)}
.up-t{color:var(--up-ink)}.down-t{color:var(--down-ink)}
.spark .line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}.spark .area{opacity:.14}
.spark.up .line{stroke:var(--up)}.spark.up .area,.spark.up circle{fill:var(--up)}
.spark.down .line{stroke:var(--down)}.spark.down .area,.spark.down circle{fill:var(--down)}
.spark.flat .line{stroke:var(--flat)}.spark.flat .area,.spark.flat circle{fill:var(--flat)}
.spark-empty{font-size:12px;color:var(--muted);font-style:italic}
.range{position:relative;height:6px;border-radius:99px;background:linear-gradient(90deg,color-mix(in srgb,var(--down) 40%,transparent),var(--surface-2),color-mix(in srgb,var(--up) 40%,transparent));min-width:110px}
.range span{position:absolute;top:-4px;width:4px;height:14px;border-radius:2px;background:var(--ink);transform:translateX(-50%)}
.range-lbl{display:flex;justify-content:space-between;font:11px var(--mono);color:var(--muted);margin-top:4px}
.term{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);box-shadow:var(--shadow);overflow:hidden}
.tabs{display:flex;overflow-x:auto;border-bottom:1px solid var(--border);background:var(--surface-2)}
.tab{flex:none;display:flex;flex-direction:column;align-items:flex-start;gap:1px;padding:10px 18px;border:0;background:none;cursor:pointer;
font:13px var(--mono);color:var(--muted);border-bottom:2px solid transparent}
.tab b{color:var(--ink)}.tab span.up{color:var(--up-ink)}.tab span.down{color:var(--down-ink)}
.tab.on{background:var(--surface);border-bottom-color:var(--blue)}
.pane{display:none;padding:16px 18px 6px}.pane.on{display:block}
.pane-h{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px;font-weight:600}
.px{font:700 22px var(--mono)}.px span{display:inline-flex;align-items:center;gap:4px;font-size:15px;margin-left:8px}
.px span.up{color:var(--up-ink)}.px span.down{color:var(--down-ink)}
.term-foot{display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;align-items:center;padding:10px 18px;border-top:1px solid var(--border);font-size:12px;color:var(--muted)}
.tg{display:inline-flex;background:var(--surface-2);border-radius:9px;padding:3px}
.seg{display:inline-flex;align-items:center;gap:6px;border:0;background:none;color:var(--muted);padding:6px 12px;border-radius:7px;cursor:pointer;font:600 12.5px var(--font)}
.seg.on{background:var(--surface);color:var(--ink);box-shadow:var(--shadow)}
svg.chart{width:100%;height:auto;display:block;overflow:visible}
.chart .grid{stroke:var(--border);stroke-width:1}.chart .zero{stroke:var(--muted);stroke-width:1}
.chart .axis{fill:var(--muted);font:11px var(--mono)}.chart .lbl{fill:var(--ink);font:600 12px var(--mono)}
.chart .val{fill:var(--ink-2);font:600 12px var(--mono)}
.chart .candleview line{stroke-width:1.2}.chart .candleview.up line{stroke:var(--up)}.chart .candleview.down line{stroke:var(--down)}
.chart .candleview.up rect{fill:var(--up)}.chart .candleview.down rect{fill:var(--down)}
.chart .vol{opacity:.35}.chart .vol.up{fill:var(--up)}.chart .vol.down{fill:var(--down)}
.chart .lineview .line{fill:none;stroke-width:2}.chart .lineview .area{opacity:.12}
.chart .lineview.up .line{stroke:var(--up)}.chart .lineview.up .area{fill:var(--up)}
.chart .lineview.down .line{stroke:var(--down)}.chart .lineview.down .area{fill:var(--down)}
.mode-candle .lineview,.mode-line .candleview{display:none}
.chart .lasttag.up{fill:var(--up)}.chart .lasttag.down{fill:var(--down)}.chart .lasttxt{fill:#fff;font:700 11px var(--mono)}
.chart .xhair{stroke:var(--muted);stroke-dasharray:3 3;opacity:0;pointer-events:none}
.chart .hit{fill:transparent}.chart [data-tip]:hover .bar{opacity:.85}
.chart .bar.up{fill:var(--up)}.chart .bar.down{fill:var(--down)}.chart .bar.flat{fill:var(--flat)}
.chart .bar.seq{fill:color-mix(in srgb,var(--teal) 45%,var(--surface))}.chart .bar.seq-strong{fill:var(--teal)}
.facts{list-style:none;padding:0;margin:0}.facts li{display:flex;align-items:center;gap:8px;padding:8px 0;border-top:1px solid var(--border);font-size:14px}
.facts li:first-child{border-top:0}
.note{font-size:12.5px;color:var(--muted);margin:8px 0 0}
.breadth{display:flex;gap:2px;height:16px;border-radius:6px;overflow:hidden;margin:6px 0 10px}
.breadth i{display:block;min-width:2px}.breadth .up{background:var(--up)}.breadth .down{background:var(--down)}.breadth .flat{background:var(--flat);opacity:.5}
.breadth-l{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--muted)}.breadth-l b{font-family:var(--mono);color:var(--ink)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);background:var(--surface);color:var(--ink-2);
padding:7px 13px;border-radius:999px;cursor:pointer;font:600 13px var(--font)}
.chip:hover{border-color:var(--pink)}.chip.on{background:var(--ink);color:var(--surface);border-color:var(--ink)}
.ev-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.event{position:relative;display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
overflow:hidden;box-shadow:var(--shadow);transition:transform .18s,box-shadow .18s}
.event:hover{transform:translateY(-3px);box-shadow:var(--shadow-h)}.event[hidden]{display:none}
.ev-cover{height:150px;background:var(--surface-2) center/cover no-repeat}
.ev-cover.none{display:grid;place-items:center;background:linear-gradient(135deg,var(--midnight),var(--pink));color:#fff}
.ev-cover.none .ic{width:40px;height:40px;opacity:.7}
.ev-date{position:absolute;top:118px;left:16px;background:var(--surface);border-radius:10px;padding:6px 10px;text-align:center;box-shadow:var(--shadow-h);line-height:1.05}
.ev-date b{display:block;font:800 20px var(--mono)}.ev-date span{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--pink);font-weight:700}
.ev-body{padding:42px 18px 18px;display:flex;flex-direction:column;gap:8px;flex:1}
.badges{display:flex;flex-wrap:wrap;gap:6px}
.event h3{margin:0;font-size:16.5px;line-height:1.3}.event h3 a{text-decoration:none}
.ev-facts{list-style:none;margin:0;padding:0;font-size:13.5px;color:var(--ink-2)}
.ev-facts li{display:flex;gap:8px;align-items:flex-start;padding:3px 0}.ev-facts .ic{margin-top:3px;color:var(--muted);width:14px;height:14px}
.ev-facts em{color:var(--muted);font-style:normal}
.ev-why{display:flex;gap:8px;margin:0;font-size:13px;color:var(--ink-2);background:var(--surface-2);padding:8px 10px;border-radius:9px}
.ev-why .ic{color:var(--pink);margin-top:2px}
.btn{margin-top:auto;display:inline-flex;align-items:center;justify-content:center;gap:8px;background:var(--ink);color:var(--surface);
text-decoration:none;font-weight:600;font-size:14px;padding:10px 14px;border-radius:10px;transition:background .15s}
.btn:hover{background:var(--blue);color:#fff}
.empty{display:flex;gap:10px;align-items:center;padding:18px;border:1px dashed var(--border);border-radius:var(--r);color:var(--muted);font-size:14px;background:var(--surface)}
.nc{margin-top:16px;border:1px dashed color-mix(in srgb,var(--muted) 50%,transparent);border-radius:var(--r);padding:14px 18px;
background:repeating-linear-gradient(135deg,transparent 0 12px,color-mix(in srgb,var(--muted) 5%,transparent) 12px 13px);font-size:13.5px;color:var(--ink-2)}
.nc-h{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.nc h3{margin:0;font-size:14.5px}.nc p{margin:8px 0 0}
.nc-links{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px}.nc-links a{display:inline-flex;gap:4px;align-items:center;color:var(--blue);font-weight:600;text-decoration:none}
.nc-links .ic{width:13px;height:13px}
.ghost table{opacity:.9}
.funds{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px}
.fund{display:inline-flex;gap:8px;align-items:center;background:var(--surface);border:1px solid var(--border);padding:8px 14px;border-radius:12px;font-weight:600;font-size:14px}
.fund .ic{color:var(--teal)}.fund em{font-style:normal;font-weight:500;font-size:12px;color:var(--muted)}
.explain ul{margin:0;padding-left:18px;font-size:13.5px;color:var(--ink-2)}.explain li{margin-bottom:8px}
.two>.nc{margin-top:0}
.banner-note{display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:color-mix(in srgb,var(--orange) 8%,var(--surface));
border:1px solid color-mix(in srgb,var(--orange) 25%,transparent);padding:10px 14px;border-radius:12px;font-size:13.5px;margin-bottom:16px}
.rd-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.rd-h{display:flex;gap:10px;align-items:center}.rd-h a{margin-left:auto;color:var(--muted)}
.sub{font:700 14px var(--mono);color:var(--orange)}
.ops{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.op{display:flex;gap:14px;text-decoration:none;background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:16px;
box-shadow:var(--shadow);transition:transform .18s,box-shadow .18s,border-color .18s}
.op:hover{transform:translateY(-2px);box-shadow:var(--shadow-h);border-color:var(--purple)}
.op-ic{display:grid;place-items:center;width:38px;height:38px;flex:none;border-radius:10px;background:color-mix(in srgb,var(--purple) 14%,transparent);color:var(--purple)}
.op-k{font:700 11px var(--font);text-transform:uppercase;letter-spacing:.1em;color:var(--purple)}
.op h3{margin:3px 0 4px;font-size:15px;line-height:1.35}.op p{margin:0;font-size:13px;color:var(--muted)}
.op-s{display:inline-flex;gap:5px;align-items:center;font-size:12px;color:var(--muted);margin-top:6px}.op-s .ic{width:12px;height:12px}
.prov{display:flex;gap:7px;align-items:flex-start;font-size:12px;color:var(--muted);margin:12px 0 0}.prov .ic{width:13px;height:13px;margin-top:2px}
.status{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);padding:18px 20px;box-shadow:var(--shadow)}
.status h2{display:flex;gap:8px;align-items:center;font-size:17px;margin:0 0 10px}
.status ul{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:6px 22px}
.status li{display:grid;grid-template-columns:18px auto;gap:2px 8px;padding:6px 0;font-size:13.5px}
.status li span{grid-column:2;color:var(--muted);font-size:12.5px}
.status .ok .ic{color:var(--up)}.status .bad .ic{color:var(--down)}.status .off .ic{color:var(--muted)}
.foot{margin-top:30px;font-size:12px;color:var(--muted);text-align:center}
#tip{position:fixed;z-index:50;pointer-events:none;background:var(--navy);color:#E2E8F0;font:12px/1.5 var(--mono);padding:8px 10px;border-radius:8px;
box-shadow:0 10px 30px rgba(0,0,0,.3);max-width:280px;border:1px solid #334155}
@media (max-width:1100px){.news-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.hero{grid-template-columns:minmax(0,1fr)}}
@media (max-width:960px){.shell{grid-template-columns:minmax(0,1fr);padding:14px 14px 50px;gap:12px}
.side{position:sticky;top:93px;z-index:20;flex-direction:row;overflow-x:auto;background:var(--bg);padding:6px 0;margin:0 -14px;padding-left:14px}
.side a{flex:none;font-size:13px;padding:7px 11px}.two{grid-template-columns:minmax(0,1fr)}.kpis{grid-template-columns:1fr 1fr}
.top-mid .wx{display:none}}
@media (max-width:640px){.news-grid{grid-template-columns:minmax(0,1fr)}.ev-grid,.desk-grid,.rd-grid,.ops,.status ul{grid-template-columns:minmax(0,1fr)}.news.featured{grid-column:auto}.news.featured h3{font-size:19px}
.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}.hero{padding:18px}.sec h2{font-size:20px}.brand span{display:none}.top{padding:10px 14px}.tile-v{font-size:18px}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}html{scroll-behavior:auto}}
@media print{.top,.tape,.side,.chips,.theme,.tg{display:none}.shell{display:block}.news,.event,.card{break-inside:avoid;box-shadow:none}}
"""

JS = r"""
(function(){
  if(window.lucide) lucide.createIcons();
  var root=document.documentElement;
  document.getElementById('theme').onclick=function(){var t=root.dataset.theme==='dark'?'light':'dark';root.dataset.theme=t;localStorage.setItem('tw-theme',t);};
  // tooltips + crosshair
  var tip=document.getElementById('tip');
  document.addEventListener('mouseover',function(ev){
    var t=ev.target.closest&&ev.target.closest('[data-tip]');
    if(!t){tip.hidden=true;return;}
    tip.innerHTML=t.getAttribute('data-tip');tip.hidden=false;
    var svg=t.closest('svg'),xh=svg&&svg.querySelector('.xhair');
    if(xh&&t.dataset.x){xh.setAttribute('x1',t.dataset.x);xh.setAttribute('x2',t.dataset.x);xh.style.opacity=1;}
  });
  document.addEventListener('mousemove',function(ev){
    if(tip.hidden)return;var w=tip.offsetWidth,h=tip.offsetHeight;
    var x=Math.min(ev.clientX+14,innerWidth-w-8),y=ev.clientY+16;if(y+h>innerHeight-8)y=ev.clientY-h-12;
    tip.style.left=x+'px';tip.style.top=y+'px';
  });
  document.querySelectorAll('svg.chart').forEach(function(s){s.addEventListener('mouseleave',function(){var x=s.querySelector('.xhair');if(x)x.style.opacity=0;tip.hidden=true;});});
  // stock tabs
  document.querySelectorAll('.tab').forEach(function(b){b.onclick=function(){
    var term=b.closest('.term');term.querySelectorAll('.tab,.pane').forEach(function(x){x.classList.remove('on');});
    b.classList.add('on');document.getElementById(b.dataset.tab).classList.add('on');};});
  document.querySelectorAll('.seg').forEach(function(b){b.onclick=function(){
    var term=b.closest('.term');term.querySelectorAll('.seg').forEach(function(x){x.classList.toggle('on',x===b);});
    term.querySelectorAll('svg.chart').forEach(function(s){s.classList.remove('mode-candle','mode-line');s.classList.add('mode-'+b.dataset.mode);});};});
  // event filters
  document.querySelectorAll('.chips').forEach(function(bar){
    var grid=bar.nextElementSibling;
    bar.querySelectorAll('.chip').forEach(function(c){c.onclick=function(){
      bar.querySelectorAll('.chip').forEach(function(x){x.classList.toggle('on',x===c);});
      grid.querySelectorAll('.event').forEach(function(ev){ev.hidden=ev.dataset.kinds.split(' ').indexOf(c.dataset.filter)<0;});};});
  });
  // active nav
  var links={};document.querySelectorAll('.side a').forEach(function(a){links[a.dataset.nav]=a;});
  if('IntersectionObserver' in window){var io=new IntersectionObserver(function(es){es.forEach(function(en){
    if(en.isIntersecting){Object.values(links).forEach(function(a){a.classList.remove('on');});var a=links[en.target.id];if(a){a.classList.add('on');}}});},
    {rootMargin:'-30% 0px -60% 0px'});
    document.querySelectorAll('main section[id]').forEach(function(s){io.observe(s);});}
})();
"""
