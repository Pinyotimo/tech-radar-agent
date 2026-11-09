import os
import re
import smtplib
import requests
import feedparser
import markdown
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from openai import OpenAI

try:
    import yfinance as yf
except ImportError:
    yf = None

# --- DUAL LLM CLIENT INITIALIZATION (LOCAL vs CLOUD) ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if GROQ_API_KEY:
    # Cloud execution via GitHub Actions (Groq Free API)
    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=GROQ_API_KEY
    )
    MODEL_NAME = "llama-3.3-70b-versatile"
    LLM_PROVIDER_NAME = "Groq (Llama 3.3 70B Cloud)"
else:
    # Local execution on laptop via Ollama
    client = OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama"
    )
    MODEL_NAME = "llama3.2"
    LLM_PROVIDER_NAME = "Ollama (Llama 3.2 Local)"

RECIPIENT_EMAIL = "steveotieno2012005@gmail.com"
SENDER_EMAIL = "steveotieno2012005@gmail.com"


# --- 1. STOCK PRICES & HTML TABLE GENERATOR ---
def generate_visual_bar(pct: float) -> str:
    """Generates a colored CSS visual trend indicator."""
    if pct > 0:
        return f"<span style='color: #10B981; font-weight: bold;'>▲ +{pct:.2f}%</span>"
    elif pct < 0:
        return f"<span style='color: #EF4444; font-weight: bold;'>▼ {pct:.2f}%</span>"
    return "<span style='color: #6B7280;'>➖ 0.00%</span>"


def get_nse_stock(symbol: str) -> str:
    """Parses live Kenya NSE stock quotes."""
    url = f"https://live.mystocks.co.ke/m/stock={symbol}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            match = re.search(r"KES\s*([\d\.]+)", res.text)
            if match:
                return f"KES {float(match.group(1)):.2f}"
    except Exception:
        pass
    return "N/A"


def get_stock_data_html() -> str:
    """Generates an eye-appealing HTML Table for Stocks."""
    print("  📊 Fetching stock prices & market fluctuations...")

    rows = []

    # Kenya NSE Stocks
    nse_stocks = {"Safaricom (SCOM)": "SCOM", "Equity Group (EQTY)": "EQTY", "KCB Group (KCB)": "KCB"}
    for name, sym in nse_stocks.items():
        price = get_nse_stock(sym)
        rows.append(f"""
        <tr>
            <td style="padding: 10px; border-bottom: 1px solid #E5E7EB;"><strong>{name}</strong></td>
            <td style="padding: 10px; border-bottom: 1px solid #E5E7EB;">{price}</td>
            <td style="padding: 10px; border-bottom: 1px solid #E5E7EB;"><span style="color:#10B981;">Live Quote</span></td>
        </tr>
        """)

    # Global Tech Stocks
    if yf is not None:
        global_tickers = {"NVIDIA (NVDA)": "NVDA", "Microsoft (MSFT)": "MSFT", "Apple (AAPL)": "AAPL",
                          "Tesla (TSLA)": "TSLA"}
        for name, sym in global_tickers.items():
            try:
                hist = yf.Ticker(sym).history(period="2d")
                if len(hist) >= 2:
                    prev, curr = hist['Close'].iloc[-2], hist['Close'].iloc[-1]
                    pct = ((curr - prev) / prev) * 100
                    trend = generate_visual_bar(pct)
                    rows.append(f"""
                    <tr>
                        <td style="padding: 10px; border-bottom: 1px solid #E5E7EB;"><strong>{name}</strong></td>
                        <td style="padding: 10px; border-bottom: 1px solid #E5E7EB;">${curr:.2f} USD</td>
                        <td style="padding: 10px; border-bottom: 1px solid #E5E7EB;">{trend}</td>
                    </tr>
                    """)
            except Exception:
                continue

    table_html = f"""
    <table style="width: 100%; border-collapse: collapse; font-family: sans-serif; margin: 15px 0; background: #FFFFFF; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
        <thead>
            <tr style="background-color: #1E293B; color: #FFFFFF; text-align: left;">
                <th style="padding: 12px;">Ticker / Asset</th>
                <th style="padding: 12px;">Price</th>
                <th style="padding: 12px;">24h Trend</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    """
    return table_html


# --- 2. GITHUB REPOS & COMMUNITY FEEDS ---
def get_top_github_repos():
    print("  ⭐ Fetching top GitHub repositories...")
    categories = {
        "UI & Design Systems": "topic:ui-components sort:stars",
        "Backend & APIs": "topic:fastapi OR topic:backend sort:stars",
        "Rust Ecosystem": "language:rust sort:stars",
        "Fintech (C# & Java)": "(topic:fintech OR topic:payments) AND (language:csharp OR language:java) sort:stars"
    }
    headers = {"User-Agent": "TechRadarAgent/1.0"}
    output = []
    for cat, query in categories.items():
        url = f"https://api.github.com/search/repositories?q={query}&per_page=3"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                output.append(f"\n#### Category: {cat}")
                for item in res.json().get("items", []):
                    output.append(
                        f"- [{item.get('full_name')}]({item.get('html_url')}) (⭐ {item.get('stargazers_count', 0):,}): {item.get('description', '')}")
        except Exception:
            continue
    return "\n".join(output)


def get_reddit_and_news():
    print("  🌐 Fetching Reddit & Tech News...")
    subreddits = ["Kenya", "KenyaTech", "ClaudeAI", "LocalLLaMA", "reactjs", "FastAPI", "rust", "fintech",
                  "cscareerquestions"]
    headers = {"User-Agent": "TechRadarAgent/1.0"}
    posts = []
    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/top.rss?t=day"
        try:
            feed = feedparser.parse(requests.get(url, headers=headers, timeout=5).content)
            if feed.entries:
                posts.append(f"\n### r/{sub}")
                for entry in feed.entries[:2]:
                    posts.append(f"- [{entry.title}]({entry.link})")
        except Exception:
            continue
    return "\n".join(posts)


# --- 3. HTML EMAIL SENDER ---
def send_email_digest(html_body: str):
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not app_password:
        print("❌ Error: GMAIL_APP_PASSWORD environment variable not set. Email not sent.")
        return

    print(f"📧 Sending formatted email digest to {RECIPIENT_EMAIL}...")

    msg = MIMEMultipart("alternative")
    today_str = datetime.now().strftime("%B %d, %Y")
    msg["Subject"] = f"🚀 Daily Tech Radar & Market Digest — {today_str}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    # Styled HTML Template Wrapper
    styled_email = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Helvetica, Arial, sans-serif; background-color: #F8FAFC; color: #334155; margin: 0; padding: 20px; }}
            .container {{ max-width: 680px; margin: 0 auto; background: #FFFFFF; padding: 30px; border-radius: 12px; border: 1px solid #E2E8F0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }}
            h1 {{ color: #0F172A; font-size: 24px; border-bottom: 3px solid #2563EB; padding-bottom: 10px; margin-top: 0; }}
            h2 {{ color: #1E293B; font-size: 18px; margin-top: 25px; border-left: 4px solid #2563EB; padding-left: 10px; }}
            h3 {{ color: #334155; font-size: 16px; margin-top: 15px; }}
            a {{ color: #2563EB; text-decoration: none; font-weight: 500; }}
            a:hover {{ text-decoration: underline; }}
            ul {{ padding-left: 20px; line-height: 1.6; }}
            li {{ margin-bottom: 8px; }}
            .footer {{ margin-top: 40px; padding-top: 15px; border-top: 1px solid #E2E8F0; font-size: 12px; color: #94A3B8; text-align: center; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Daily Tech Radar & Market Digest</h1>
            <p style="color: #64748B; font-size: 14px;">Generated on {today_str} for Steve Otieno</p>
            {html_body}
            <div class="footer">
                Automated by Steve's Personal AI Agent • Powered by {LLM_PROVIDER_NAME} & Python
            </div>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(styled_email, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, app_password)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print("✅ Email successfully sent!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


# --- 4. MAIN AGENT ORCHESTRATOR ---
def run_agent():
    print(f"🤖 Agent execution started using model: {MODEL_NAME} ({LLM_PROVIDER_NAME})...")
    print(" Gathering intelligence feeds...\n")

    stock_html_table = get_stock_data_html()
    github_data = get_top_github_repos()
    community_data = get_reddit_and_news()

    raw_payload = f"""
=== TOP GITHUB REPOS ===
{github_data}

=== COMMUNITY & KENYA TECH ===
{community_data}
"""

    print(f"\n🧠 Processing feeds with {LLM_PROVIDER_NAME}...")

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {
                "role": "system",
                "content": "You are a tech intelligence agent for a Full-Stack Engineer (React, FastAPI, Python, Fintech). Summarize the raw updates into clean Markdown sections: 🇰🇪 Kenya Tech & Jobs, ⭐ Top Repositories (UI, Backend, Rust, Fintech C#/Java), and 💼 Global Tech Trends. Filter out fluff and keep markdown links intact."
            },
            {"role": "user", "content": f"Summarize these updates:\n\n{raw_payload}"}
        ]
    )

    llm_markdown = response.choices[0].message.content

    # Convert LLM Markdown output into clean HTML
    llm_html_content = markdown.markdown(llm_markdown)

    # Combine Stock HTML Table + LLM Summarized Content
    full_html_report = f"""
    <h2>📊 Stock Prices & Market Performance (Kenya NSE & Global)</h2>
    {stock_html_table}
    {llm_html_content}
    """

    # Dispatch to Gmail
    send_email_digest(full_html_report)


if __name__ == "__main__":
    run_agent()