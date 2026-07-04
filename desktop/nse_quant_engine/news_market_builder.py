"""
News / Market Context Builder - Stage 3.4.1
===========================================

Fetches recent market/news context after latest_scores.csv exists.

Patch improvements:
    1. Adds Published_Date, Age_Days, and Recency_Bucket.
    2. Uses recent Google News queries with when:45d.
    3. Separates recent headlines from stale fallback headlines.
    4. Makes old candidate headlines visible as stale rather than pretending
       they are current.

It does NOT alter quant scores. It creates a context pack for AI review.

Reads:
    output/latest_scores.csv
    output/cross_sectional_validation_report.md

Writes:
    data/news_latest.csv
    data/top_candidate_news.csv
    output/news_market_context.md

Run after trade_plan_builder.py:
    python news_market_builder.py
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
import requests
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

LATEST_SCORES = OUTPUT_DIR / "latest_scores.csv"
TRADE_PLAN_REPORT = OUTPUT_DIR / "trade_plan_report.md"
CROSS_VALIDATION_REPORT = OUTPUT_DIR / "cross_sectional_validation_report.md"
NEWS_OUT = DATA_DIR / "news_latest.csv"
TOP_CANDIDATE_NEWS_OUT = DATA_DIR / "top_candidate_news.csv"
MARKET_CONTEXT_OUT = OUTPUT_DIR / "news_market_context.md"

HEADERS = {"User-Agent": "Mozilla/5.0"}

RECENT_DAYS = 45

MARKET_QUERIES = [
    "Nifty 50 market outlook India",
    "Nifty Next 50 market outlook India",
    "India stock market Nifty FII flows RBI inflation crude rupee",
    "RBI policy India stock market outlook",
    "Nasdaq US Fed India market impact",
    "crude oil rupee India equity market impact",
]


def google_news_rss_url(query: str, recent_days: int = RECENT_DAYS) -> str:
    q = f"{query} when:{recent_days}d"
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"


def parse_pub_date(value: str):
    if not value:
        return pd.NaT
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return pd.Timestamp(dt)
    except Exception:
        return pd.NaT


def add_recency_fields(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    now = pd.Timestamp.now()
    df["Published_Date"] = df["Published"].map(parse_pub_date)
    df["Age_Days"] = (now - df["Published_Date"]).dt.days
    df["Recency_Bucket"] = pd.cut(
        df["Age_Days"],
        bins=[-999999, 14, 45, 90, 999999],
        labels=["Recent_0_14D", "Current_15_45D", "Older_46_90D", "Stale_90DPlus"],
    ).astype(str)
    df.loc[df["Published_Date"].isna(), "Recency_Bucket"] = "Unknown_Date"
    return df


def fetch_rss(query: str, limit: int = 10) -> list[dict]:
    url = google_news_rss_url(query)
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as exc:
        return [{"Query": query, "Title": f"FETCH_ERROR: {exc}", "Link": "", "Published": "", "Source": ""}]

    items = []
    seen_titles = set()
    for item in root.findall("./channel/item"):
        title = item.findtext("title", default="").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        link = item.findtext("link", default="")
        published = item.findtext("pubDate", default="")
        source_node = item.find("source")
        source = source_node.text if source_node is not None else ""
        items.append({"Query": query, "Title": title, "Link": link, "Published": published, "Source": source})
        if len(items) >= limit:
            break
    return items


def candidate_queries(latest: pd.DataFrame, top_n: int = 12) -> list[tuple[str, str]]:
    eligible = latest.copy()
    if "Opportunity_Eligible" in eligible.columns:
        eligible = eligible[eligible["Opportunity_Eligible"].astype(str).str.lower().eq("yes")].copy()
    if "Confidence_Adjusted_Score" in eligible.columns:
        eligible["Confidence_Adjusted_Score"] = pd.to_numeric(eligible["Confidence_Adjusted_Score"], errors="coerce")
        eligible = eligible.sort_values("Confidence_Adjusted_Score", ascending=False)
    elif "Final_Score" in eligible.columns:
        eligible["Final_Score"] = pd.to_numeric(eligible["Final_Score"], errors="coerce")
        eligible = eligible.sort_values("Final_Score", ascending=False)
    eligible = eligible.head(top_n)

    queries = []
    for _, row in eligible.iterrows():
        name = str(row.get("Name", "")).strip()
        symbol = str(row.get("Raw_Symbol", row.get("Symbol", ""))).replace(".NS", "")
        query = f"{name} {symbol} stock news India" if name else f"{symbol} stock news India"
        queries.append((str(row.get("Symbol")), query))
    return queries


def md_table(df: pd.DataFrame, cols: list[str], max_rows: int = 10) -> str:
    if df.empty:
        return "_No rows._"
    temp = df.copy()
    for col in cols:
        if col not in temp.columns:
            temp[col] = ""
    return temp[cols].head(max_rows).to_markdown(index=False)


def main() -> None:
    print("News / Market Context Builder - Stage 3.4.1")
    print("===========================================")

    if not LATEST_SCORES.exists():
        raise FileNotFoundError("output/latest_scores.csv not found. Run nse_quant_engine.py first.")

    latest = pd.read_csv(LATEST_SCORES)

    print("Fetching market context...")
    rows = []
    for q in MARKET_QUERIES:
        rows.extend(fetch_rss(q, limit=8))
    market_news = add_recency_fields(pd.DataFrame(rows))
    if not market_news.empty:
        market_news = market_news.sort_values(["Published_Date"], ascending=False, na_position="last")
    market_news.to_csv(NEWS_OUT, index=False)

    print("Fetching top candidate news...")
    candidate_rows = []
    for symbol, q in candidate_queries(latest, top_n=12):
        for item in fetch_rss(q, limit=5):
            item["Symbol"] = symbol
            candidate_rows.append(item)
    candidate_news = add_recency_fields(pd.DataFrame(candidate_rows))
    if not candidate_news.empty:
        candidate_news = candidate_news.sort_values(["Symbol", "Published_Date"], ascending=[True, False], na_position="last")
    candidate_news.to_csv(TOP_CANDIDATE_NEWS_OUT, index=False)

    validation_text = CROSS_VALIDATION_REPORT.read_text(encoding="utf-8", errors="ignore") if CROSS_VALIDATION_REPORT.exists() else ""
    trade_plan_text = TRADE_PLAN_REPORT.read_text(encoding="utf-8", errors="ignore") if TRADE_PLAN_REPORT.exists() else ""

    recent_candidate_news = candidate_news[candidate_news["Recency_Bucket"].isin(["Recent_0_14D", "Current_15_45D"])].copy() if not candidate_news.empty else pd.DataFrame()
    stale_candidate_news = candidate_news[candidate_news["Recency_Bucket"].isin(["Older_46_90D", "Stale_90DPlus"])].copy() if not candidate_news.empty else pd.DataFrame()

    lines = []
    lines.append("# News and Market Context Pack")
    lines.append("")
    lines.append("This file is context for AI review. It does not change the quant scores.")
    lines.append("")
    lines.append("News recency rule: recent/current headlines are prioritized. Older headlines are shown only as stale context, not fresh catalysts.")
    lines.append("")

    if validation_text:
        lines.append("## Current Validation Summary")
        lines.append("")
        lines.append(validation_text[:3000])
        lines.append("")

    if trade_plan_text:
        lines.append("## Trade Plan Validation Stamp")
        lines.append("")
        lines.append(trade_plan_text[:1200])
        lines.append("")

    lines.append("## Recent Market Context Headlines")
    lines.append(md_table(
        market_news[market_news["Recency_Bucket"].isin(["Recent_0_14D", "Current_15_45D"])],
        ["Query", "Title", "Source", "Published", "Recency_Bucket"],
        25,
    ))
    lines.append("")

    lines.append("## Recent Candidate-Specific Headlines")
    lines.append(md_table(
        recent_candidate_news,
        ["Symbol", "Title", "Source", "Published", "Recency_Bucket"],
        30,
    ))
    lines.append("")

    lines.append("## Stale Candidate Headlines")
    lines.append("These are old context, not fresh catalysts.")
    lines.append(md_table(
        stale_candidate_news,
        ["Symbol", "Title", "Source", "Published", "Recency_Bucket"],
        30,
    ))
    lines.append("")

    lines.append("## How to use this")
    lines.append("- Upload this file with latest_scores_validated.xlsx and trade_plan_latest.xlsx.")
    lines.append("- Ask AI to flag candidates with negative or contradictory RECENT news.")
    lines.append("- Stale headlines can be risk context, but should not be treated as current catalysts.")
    lines.append("- Do not let headlines override the score mechanically; use them as final review context.")
    MARKET_CONTEXT_OUT.write_text("\n".join(lines), encoding="utf-8")

    print(f"Saved: {NEWS_OUT}")
    print(f"Saved: {TOP_CANDIDATE_NEWS_OUT}")
    print(f"Saved: {MARKET_CONTEXT_OUT}")


if __name__ == "__main__":
    main()
