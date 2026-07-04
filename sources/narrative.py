"""
sources/narrative.py — Validasi narasi viral lewat proxy GRATIS lintas platform.

Karena X & Instagram tak bisa diakses gratis-stabil, kita pakai proxy yang
menangkap keviralan lintas platform:
  - Google Trends (pytrends): lonjakan search 7 hari -> sinyal terkuat & "tahan lama".
  - YouTube Data API v3 (gratis 10k unit/hari): video + view 72 jam terakhir.
  - Google News RSS (gratis): ada pemberitaan atau tidak.

Semua dibungkus try/except: sumber unofficial bisa mati kapan saja -> degrade
gracefully (return skor 0 + tandai tidak tersedia), JANGAN crash run.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import config
from sources import http

log = logging.getLogger("narrative")


# ---------------------------------------------------------------------------
# Deteksi kategori narasi dari nama + simbol token (label saja, bukan gate)
# ---------------------------------------------------------------------------
_NARRATIVE_PATTERNS = [
    ("trump-elon", r"\b(trump|elon|musk|maga|doge)\b"),
    ("celebrity", r"\b(kanye|drake|taylor|ronaldo|messi|selena)\b"),
    ("justice", r"\bjustice[\s-]?for\b|\bfree\b"),
    ("ai-tech", r"\b(ai|gpt|agent|robot|neural|quantum)\b"),
    ("bags", r"\b(bags?|money|rich|wealth|gold)\b"),
    ("news", r"\b(news|breaking|update)\b"),
    ("animal", r"\b(dog|cat|frog|pepe|shib|inu|bonk|wif)\b"),
]


def detect_category(name: str, symbol: str) -> str:
    text = f"{name} {symbol}".lower()
    for label, pat in _NARRATIVE_PATTERNS:
        if re.search(pat, text):
            return label
    return "unknown"


# ---------------------------------------------------------------------------
# Google Trends via pytrends (bobot besar: paling mewakili "tahan lama")
# ---------------------------------------------------------------------------
def google_trends_signal(keyword: str) -> Dict[str, Any]:
    """
    Return { available, rising: bool, sustained: bool, avg: float }.
    rising    = tren 7d menaik (perbandingan separuh akhir vs awal).
    sustained = kurva belum anjlok di titik terakhir (narasi masih hidup).
    """
    out = {"available": False, "rising": False, "sustained": False, "avg": 0.0}
    if not keyword or len(keyword) < 2:
        return out
    try:
        from pytrends.request import TrendReq  # import lokal: opsional

        pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 15))
        pytrends.build_payload([keyword], timeframe=config.GOOGLE_TRENDS_TIMEFRAME)
        df = pytrends.interest_over_time()
        if df is None or df.empty or keyword not in df:
            return out
        series = [float(x) for x in df[keyword].tolist() if x is not None]
        if len(series) < 4:
            return out
        half = len(series) // 2
        first_avg = sum(series[:half]) / max(half, 1)
        second_avg = sum(series[half:]) / max(len(series) - half, 1)
        last = series[-1]
        peak = max(series) or 1.0
        out.update(
            {
                "available": True,
                "avg": round(sum(series) / len(series), 1),
                "rising": second_avg >= first_avg,
                # "tahan lama": titik terakhir masih >= 50% puncak (belum anjlok)
                "sustained": last >= 0.5 * peak,
            }
        )
    except Exception as e:  # noqa: BLE001
        log.info("Google Trends gagal utk '%s': %s (degrade)", keyword, e)
    return out


# ---------------------------------------------------------------------------
# YouTube Data API v3 (butuh YOUTUBE_API_KEY; opsional)
# ---------------------------------------------------------------------------
def youtube_signal(keyword: str) -> Dict[str, Any]:
    """
    Return { available, video_count, total_views }.
    Hitung video baru dalam YOUTUBE_LOOKBACK_HOURS terakhir + total view-nya.
    """
    out = {"available": False, "video_count": 0, "total_views": 0}
    if not config.YOUTUBE_API_KEY or not keyword:
        return out
    try:
        after = (
            datetime.now(timezone.utc) - timedelta(hours=config.YOUTUBE_LOOKBACK_HOURS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        search = http.get_json(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "id",
                "q": keyword,
                "type": "video",
                "order": "date",
                "publishedAfter": after,
                "maxResults": 25,
                "key": config.YOUTUBE_API_KEY,
            },
        )
        if not search:
            return out
        ids = [
            it["id"]["videoId"]
            for it in search.get("items", [])
            if it.get("id", {}).get("videoId")
        ]
        out["video_count"] = len(ids)
        out["available"] = True
        if ids:
            stats = http.get_json(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "statistics", "id": ",".join(ids), "key": config.YOUTUBE_API_KEY},
            )
            total = 0
            for it in (stats or {}).get("items", []):
                total += int(it.get("statistics", {}).get("viewCount", 0) or 0)
            out["total_views"] = total
    except Exception as e:  # noqa: BLE001
        log.info("YouTube gagal utk '%s': %s (degrade)", keyword, e)
    return out


# ---------------------------------------------------------------------------
# Google News RSS (gratis, no key; bobot kecil)
# ---------------------------------------------------------------------------
def google_news_signal(keyword: str) -> Dict[str, Any]:
    """Return { available, article_count }."""
    out = {"available": False, "article_count": 0}
    if not keyword:
        return out
    try:
        url = f"https://news.google.com/rss/search?q={quote_plus(keyword)}&hl=en-US&gl=US&ceid=US:en"
        resp = http._session.get(url, timeout=config.HTTP_TIMEOUT)  # RSS = XML, bukan JSON
        if resp.status_code != 200:
            return out
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        out.update({"available": True, "article_count": len(items)})
    except Exception as e:  # noqa: BLE001
        log.info("Google News gagal utk '%s': %s (degrade)", keyword, e)
    return out


# ---------------------------------------------------------------------------
# Agregasi narasi -> label KUAT/SEDANG/LEMAH
# ---------------------------------------------------------------------------
def evaluate_narrative(name: str, symbol: str) -> Dict[str, Any]:
    """
    Gabungkan semua sinyal narasi. Return dict siap dipakai scoring & notif:
      { category, label, sustained, trends, youtube, news, keyword }
    Skor 0-1 (dipakai scoring.py utk dikali bobot).
    """
    if not config.NARRATIVE_ENABLED:
        return {"category": "n/a", "label": "OFF", "score": 0.0, "sustained": False,
                "trends": {}, "youtube": {}, "news": {}, "keyword": symbol}

    # Keyword: pakai simbol bila cukup unik, else nama.
    keyword = symbol if symbol and len(symbol) >= 3 and symbol != "?" else name
    category = detect_category(name, symbol)

    trends = google_trends_signal(keyword)
    youtube = youtube_signal(keyword)
    news = google_news_signal(keyword)

    # Skoring proxy (bobot: trends 0.6, youtube 0.3, news 0.1)
    score = 0.0
    if trends.get("available"):
        if trends.get("rising"):
            score += 0.4
        if trends.get("sustained"):
            score += 0.2
    if youtube.get("available"):
        vc = youtube.get("video_count", 0)
        if vc >= 15:
            score += 0.3
        elif vc >= 5:
            score += 0.15
    if news.get("available") and news.get("article_count", 0) >= 3:
        score += 0.1

    if score >= 0.6:
        label = "KUAT"
    elif score >= 0.3:
        label = "SEDANG"
    else:
        label = "LEMAH"

    return {
        "category": category,
        "label": label,
        "score": round(min(score, 1.0), 2),
        "sustained": bool(trends.get("sustained")),
        "trends": trends,
        "youtube": youtube,
        "news": news,
        "keyword": keyword,
    }
