"""
sources/narrative.py — Validasi narasi viral: KUALITATIF + KUANTITATIF, lintas platform.

Tujuan (sesuai permintaan user): jangan cuma bilang "narasi kuat/lemah", tapi
pisahkan dua pertanyaan yang beda:
  1. VIRALITAS  -> seberapa RAMAI narasi ini sekarang (breadth lintas platform +
     volume mentah: views, post, artikel) DAN seberapa BERAGAM komunitasnya
     (proxy kualitatif "banyak komunitas/meme variasi" ala kasus Pepe: dihitung
     dari jumlah subreddit/channel/domain berita yang BERBEDA, bukan cuma total).
  2. DAYA TAHAN -> apakah masih hidup setelah beberapa hari (TAHAN LAMA) atau
     cuma spike sesaat lalu mati (SESAAT, sinyal pump-dump narasi).

Sumber data (semua GRATIS):
  - Google Trends (pytrends, no key)  : lonjakan + apakah belum anjlok (7 hari).
  - YouTube Data API v3 (opsional key): video + view + jumlah CHANNEL berbeda.
  - Reddit search.json (no key)       : post + upvote + komentar + jumlah
    SUBREDDIT berbeda + apakah masih ada post baru 24 jam terakhir (durability).
  - Google News RSS (no key)          : artikel + jumlah domain sumber berbeda.

Keterbatasan yang JUJUR (sesuai batasan awal, TIDAK di-scraping):
  X/Twitter, Instagram, Facebook, TikTok TIDAK punya API pencarian gratis yang
  stabil (X API kini berbayar; IG/FB/TikTok tak ada API publik). Untuk itu bot
  menyediakan LINK siap-klik (lihat notify.py) supaya user cek manual "vibe"-nya,
  bukan angka otomatis.

Semua panggilan dibungkus try/except: sumber unofficial (pytrends, Reddit json)
bisa mati/berubah kapan saja -> degrade gracefully (skor 0, tandai tak tersedia),
JANGAN crash run.
"""

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree as ET

import config
from sources import http

log = logging.getLogger("narrative")

# Reddit mewajibkan User-Agent unik & deskriptif (bukan default requests/browser)
# supaya tak kena limit lebih ketat -- lihat https://github.com/reddit-archive/reddit/wiki/API
_REDDIT_HEADERS = {
    "User-Agent": "meteora-lp-bot/1.0 (github.com/querastudio/meteora-lp-bot; screening bot)"
}


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
# Filter relevansi: simbol umum (mis. $CHANCE, $HOPE) match kata Inggris biasa
# di berita/post/video yang SAMA SEKALI bukan soal token ini (mis. "Germany's
# Second Chance for Growth"). Tanpa filter ini breadth/volume/evidence jadi
# noise murni -- bukan sinyal narasi token, false "VIRAL".
#
# Kasus lebih halus (nyata: token cow-emoji "$0x" di pump.fun): simbol yang
# BERTABRAKAN dgn ticker proyek crypto lain yg SUDAH ESTABLISHED (mis. "0x"
# jg ticker 0x Protocol/ZRX, proyek DeFi asli tak berkaitan). Keyword generik
# spt "crypto"/"blockchain"/"market cap" JUSTRU lolos di berita proyek lain
# itu -- bukan sinyal token kita. Selain itu keyword pendek spt "coin"/
# "token" match sbg SUBSTRING di kata tak berkaitan ("CoinMarketCap",
# "Tokenized") kalau dicek pakai `in` biasa, bukan word-boundary.
#
# Fix: keyword wajib SPESIFIK Solana-memecoin (bukan istilah crypto umum yg
# bisa dipakai proyek established mana pun), dan dicocokkan pakai regex
# word-boundary supaya tak ketipu substring semacam itu.
# ---------------------------------------------------------------------------
_MEMECOIN_CONTEXT_KEYWORDS = (
    "solana", "pump.fun", "pumpfun", "memecoin", "meme coin", "meme-coin",
    "dexscreener", "raydium", "moonshot", "bonding curve", "rug pull",
    "gmgn", "birdeye", "meteora", "dlmm", "shitcoin", "degen",
)
_MEMECOIN_CONTEXT_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in _MEMECOIN_CONTEXT_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def _looks_crypto_related(text: str, symbol: str) -> bool:
    """
    True bila teks pakai cashtag simbolnya (word-boundary, bukan substring)
    ATAU eksplisit soal konteks Solana-memecoin. SENGAJA tak pakai istilah
    crypto generik ("crypto", "token", "blockchain", dst) -- proyek established
    tak berkaitan yg kebetulan bertabrakan ticker (mis. $0x vs 0x Protocol/ZRX)
    pasti juga pakai istilah itu, jadi tak diskriminatif.
    """
    if not text:
        return False
    if re.search(rf"\${re.escape(symbol)}\b", text, re.IGNORECASE):
        return True
    return bool(_MEMECOIN_CONTEXT_RE.search(text))


# ---------------------------------------------------------------------------
# Google Trends via pytrends (sinyal daya tahan terkuat: minat msh hidup?)
# ---------------------------------------------------------------------------
def google_trends_signal(keyword: str) -> Dict[str, Any]:
    """
    Return { available, rising, sustained, avg }.
    rising    = tren 7d menaik (perbandingan separuh akhir vs awal).
    sustained = titik terakhir masih >= 50% puncak (narasi belum anjlok/mati).
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
    Return { available, video_count, total_views, channel_count }.
    channel_count = jumlah CHANNEL BERBEDA yang membuat video -> proxy
    "banyak kreator/komunitas ikut membahas" (bukan cuma 1 channel spam).
    """
    out = {"available": False, "video_count": 0, "total_views": 0, "channel_count": 0}
    if not config.YOUTUBE_API_KEY or not keyword:
        return out
    try:
        after = (
            datetime.now(timezone.utc) - timedelta(hours=config.YOUTUBE_LOOKBACK_HOURS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        search = http.get_json(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "id,snippet",
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
        items = search.get("items", [])
        items = [
            it for it in items
            if _looks_crypto_related((it.get("snippet") or {}).get("title", ""), keyword)
        ]
        ids = [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]
        channels = {
            it["snippet"]["channelId"]
            for it in items
            if it.get("snippet", {}).get("channelId")
        }
        out["video_count"] = len(ids)
        out["channel_count"] = len(channels)
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
# Reddit search.json (gratis, no key) — post/score/comment + diversitas komunitas
# ---------------------------------------------------------------------------
def reddit_signal(keyword: str) -> Dict[str, Any]:
    """
    Return { available, post_count, total_score, total_comments,
             subreddit_count, posts_last24h }.

    subreddit_count = jumlah SUBREDDIT BERBEDA yang membahas -> proxy paling
    langsung utk "banyak komunitas" (persis kasus Pepe: dibahas di r/dogecoin,
    r/cryptocurrency, r/pepecoin, r/memecoins, dst -- bukan cuma 1 forum).

    posts_last24h > 0 (padahal window pencarian 7 hari) -> narasi masih hidup
    HARI INI, bukan cuma sisa spike beberapa hari lalu -> sinyal daya tahan.

    Endpoint publik Reddit ini TIDAK RESMI didokumentasikan untuk otomasi berat
    -- bisa di-rate-limit/berubah kapan saja. Degrade gracefully bila gagal.
    """
    out = {
        "available": False,
        "post_count": 0,
        "total_score": 0,
        "total_comments": 0,
        "subreddit_count": 0,
        "posts_last24h": 0,
        "top_posts": [],  # konteks kualitatif: judul post ter-upvote (lihat notify.py)
    }
    if not config.REDDIT_ENABLED or not keyword:
        return out
    try:
        data = http.get_json(
            "https://www.reddit.com/search.json",
            params={
                "q": keyword,
                "sort": "new",
                "limit": 100,
                "t": "week",
                "restrict_sr": "false",
            },
            headers=_REDDIT_HEADERS,
        )
        if not data:
            return out
        children = (data.get("data") or {}).get("children") or []
        if not children:
            out["available"] = True  # call sukses, memang tak ada post
            return out

        now = datetime.now(timezone.utc).timestamp()
        subreddits: Counter = Counter()
        total_score = 0
        total_comments = 0
        posts_24h = 0
        posts_raw: List[Dict[str, Any]] = []
        for c in children:
            d = c.get("data") or {}
            if not _looks_crypto_related(
                f"{d.get('title', '')} {d.get('selftext', '')}", keyword
            ):
                continue
            sub = d.get("subreddit")
            if sub:
                subreddits[sub] += 1
            score = int(d.get("score", 0) or 0)
            total_score += score
            total_comments += int(d.get("num_comments", 0) or 0)
            created = d.get("created_utc")
            if created and (now - float(created)) <= 86400:
                posts_24h += 1
            title = d.get("title")
            if title:
                posts_raw.append(
                    {
                        "title": title,
                        "score": score,
                        "subreddit": sub or "?",
                        "url": f"https://www.reddit.com{d.get('permalink', '')}",
                    }
                )

        # Konteks kualitatif: 2 post PALING banyak upvote (bukan cuma terbaru)
        # -- ini yang dipakai notify.py utk kasih "penjelasan mengenai token".
        posts_raw.sort(key=lambda p: p["score"], reverse=True)
        out["top_posts"] = posts_raw[:2]

        out.update(
            {
                "available": True,
                "post_count": len(posts_raw),
                "total_score": total_score,
                "total_comments": total_comments,
                "subreddit_count": len(subreddits),
                "posts_last24h": posts_24h,
            }
        )
    except Exception as e:  # noqa: BLE001
        log.info("Reddit gagal utk '%s': %s (degrade)", keyword, e)
    return out


# ---------------------------------------------------------------------------
# Google News RSS (gratis, no key) — artikel + diversitas domain sumber
# ---------------------------------------------------------------------------
def google_news_signal(keyword: str) -> Dict[str, Any]:
    """Return { available, article_count, domain_count, top_articles }."""
    out = {"available": False, "article_count": 0, "domain_count": 0, "top_articles": []}
    if not keyword:
        return out
    try:
        url = f"https://news.google.com/rss/search?q={quote_plus(keyword)}&hl=en-US&gl=US&ceid=US:en"
        resp = http._session.get(url, timeout=config.HTTP_TIMEOUT)  # RSS = XML, bukan JSON
        if resp.status_code != 200:
            return out
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")
        domains = set()
        articles: List[Dict[str, Any]] = []
        for it in items:
            title_el = it.find("title")
            title_text = title_el.text if title_el is not None and title_el.text else ""
            # RSS search Google News cuma cocokkan kata literal -- simbol yg
            # kebetulan kata Inggris umum (mis. $CHANCE) bisa match berita
            # sama sekali tak relevan (lihat _looks_crypto_related).
            if not _looks_crypto_related(title_text, keyword):
                continue
            src = it.find("source")
            source_name = src.text if src is not None and src.text else "?"
            if src is not None and src.get("url"):
                try:
                    domains.add(urlparse(src.get("url")).netloc)
                except ValueError:
                    pass
            link_el = it.find("link")
            if title_text:
                articles.append(
                    {
                        "title": title_text,
                        "source": source_name,
                        "url": link_el.text if link_el is not None else "",
                    }
                )
        out.update(
            {
                "available": True,
                "article_count": len(articles),
                "domain_count": len(domains),
                # Google News RSS sudah terurut relevansi -> ambil 2 teratas
                # sbg konteks kualitatif (dipakai notify.py).
                "top_articles": articles[:2],
            }
        )
    except Exception as e:  # noqa: BLE001
        log.info("Google News gagal utk '%s': %s (degrade)", keyword, e)
    return out


# ---------------------------------------------------------------------------
# Agregasi: pisahkan VIRALITAS (breadth+volume+diversitas) vs DAYA TAHAN
# ---------------------------------------------------------------------------
def _norm(value: float, cap: float) -> float:
    """Normalisasi 0-1 dengan cap lembut (hindari 1 metrik whale mendominasi)."""
    if cap <= 0:
        return 0.0
    return max(0.0, min(value / cap, 1.0))


def evaluate_narrative(name: str, symbol: str) -> Dict[str, Any]:
    """
    Gabungkan semua sinyal jadi dua sumbu terpisah + rincian kuantitatif mentah
    (dipakai notify.py utk tampilkan angka asli, bukan cuma label).

    Return:
      {
        category, keyword, score (0-1, dipakai scoring.py),
        viral_label ("SANGAT VIRAL"/"VIRAL"/"SEDANG"/"LEMAH"),
        durability_label ("TAHAN LAMA"/"SEDANG"/"SESAAT"),
        breadth_score, volume_score, diversity_score, durability_score (0-1),
        insights: [str, ...]  -- kalimat kualitatif otomatis (rule-based),
        trends, youtube, reddit, news  -- data mentah per sumber,
      }
    """
    empty = {
        "category": "n/a", "keyword": symbol, "score": 0.0,
        "viral_label": "OFF", "durability_label": "OFF",
        "breadth_score": 0.0, "volume_score": 0.0, "diversity_score": 0.0,
        "durability_score": 0.0, "insights": [], "evidence": [],
        "trends": {}, "youtube": {}, "reddit": {}, "news": {},
    }
    if not config.NARRATIVE_ENABLED:
        return empty

    keyword = symbol if symbol and len(symbol) >= 3 and symbol != "?" else name
    category = detect_category(name, symbol)

    trends = google_trends_signal(keyword)
    youtube = youtube_signal(keyword)
    reddit = reddit_signal(keyword)
    news = google_news_signal(keyword)

    # --- BREADTH: berapa dari 4 platform yang menunjukkan aktivitas nyata ---
    active_flags = [
        trends.get("available") and trends.get("rising"),
        youtube.get("available") and youtube.get("video_count", 0) >= config.NARRATIVE_MIN_YOUTUBE_VIDEOS,
        reddit.get("available") and reddit.get("post_count", 0) >= config.NARRATIVE_MIN_REDDIT_POSTS,
        news.get("available") and news.get("article_count", 0) >= config.NARRATIVE_MIN_NEWS_ARTICLES,
    ]
    breadth_score = sum(1 for f in active_flags if f) / 4.0

    # --- VOLUME: angka mentah dinormalisasi (kuantitatif) ---
    vol_parts: List[float] = []
    if trends.get("available"):
        vol_parts.append(_norm(trends.get("avg", 0.0), 100.0))
    if youtube.get("available"):
        vol_parts.append(_norm(youtube.get("total_views", 0), config.NARRATIVE_YOUTUBE_VIEWS_CAP))
    if reddit.get("available") and reddit.get("post_count", 0) > 0:
        vol_parts.append(_norm(reddit.get("total_score", 0), config.NARRATIVE_REDDIT_SCORE_CAP))
    if news.get("available"):
        vol_parts.append(_norm(news.get("article_count", 0), config.NARRATIVE_NEWS_ARTICLES_CAP))
    volume_score = sum(vol_parts) / len(vol_parts) if vol_parts else 0.0

    # --- DIVERSITAS KOMUNITAS: proxy kualitatif "banyak komunitas/variasi" ---
    div_parts: List[float] = []
    if reddit.get("available") and reddit.get("post_count", 0) > 0:
        div_parts.append(_norm(reddit.get("subreddit_count", 0), config.NARRATIVE_REDDIT_SUBREDDIT_CAP))
    if youtube.get("available") and youtube.get("video_count", 0) > 0:
        div_parts.append(_norm(youtube.get("channel_count", 0), config.NARRATIVE_YOUTUBE_CHANNEL_CAP))
    if news.get("available") and news.get("article_count", 0) > 0:
        div_parts.append(_norm(news.get("domain_count", 0), config.NARRATIVE_NEWS_DOMAIN_CAP))
    diversity_score = sum(div_parts) / len(div_parts) if div_parts else 0.0

    # --- DAYA TAHAN: masih hidup setelah beberapa hari, bukan cuma spike ---
    dur_parts: List[float] = []
    if trends.get("available"):
        dur_parts.append(1.0 if trends.get("sustained") else 0.2)
    if reddit.get("available") and reddit.get("post_count", 0) > 0:
        # masih ada post BARU 24 jam terakhir dalam window 7 hari -> msh hidup.
        dur_parts.append(1.0 if reddit.get("posts_last24h", 0) > 0 else 0.3)
    durability_score = sum(dur_parts) / len(dur_parts) if dur_parts else 0.0

    # --- Skor komposit (dipakai scoring.py, bobot "narrative") ---
    score = (
        0.30 * breadth_score
        + 0.30 * volume_score
        + 0.15 * diversity_score
        + 0.25 * durability_score
    )

    # --- Label ---
    if breadth_score >= 0.75 and volume_score >= 0.6:
        viral_label = "🔥 SANGAT VIRAL"
    elif breadth_score >= 0.5:
        viral_label = "VIRAL"
    elif breadth_score >= 0.25:
        viral_label = "SEDANG"
    else:
        viral_label = "LEMAH"

    if durability_score >= 0.7:
        durability_label = "TAHAN LAMA"
    elif durability_score >= 0.4:
        durability_label = "SEDANG"
    else:
        durability_label = "SESAAT (waspada pump lalu mati)"

    # --- Insight kualitatif otomatis (rule-based, bukan LLM -> deterministik) ---
    insights: List[str] = []
    n_active = sum(1 for f in active_flags if f)
    if n_active >= 3:
        insights.append(f"aktif di {n_active}/4 platform (bukan 1 sumber saja)")
    elif n_active <= 1:
        insights.append("cuma aktif di 1 platform atau kurang -- narasi sempit")

    total_communities = reddit.get("subreddit_count", 0) + youtube.get("channel_count", 0)
    if total_communities >= 8:
        insights.append(
            f"{reddit.get('subreddit_count',0)} subreddit & {youtube.get('channel_count',0)} channel "
            f"berbeda ikut bahas -- indikasi organik lintas komunitas"
        )
    elif total_communities <= 2 and (reddit.get("available") or youtube.get("available")):
        insights.append("sumber pembahasan masih sempit (sedikit komunitas/kreator berbeda)")

    if reddit.get("available") and reddit.get("post_count", 0) > 0 and reddit.get("posts_last24h", 0) == 0:
        insights.append("Reddit: tak ada post baru 24 jam terakhir -- momentum mereda")

    if trends.get("available") and not trends.get("sustained"):
        insights.append("Google Trends sudah turun jauh dari puncak -- minat mereda")

    # --- Evidence: kutipan NYATA (bukan karangan) sbg "penjelasan mengenai
    # tokennya" -- judul post/artikel asli yg paling relevan, biar user tahu
    # KONTEKS narasinya (mis. "siapa yg bahas, tentang apa"), bukan cuma angka.
    evidence: List[Dict[str, str]] = []
    for p in reddit.get("top_posts", []):
        evidence.append(
            {"text": p["title"], "source": f"Reddit r/{p['subreddit']} ({p['score']} upvote)", "url": p["url"]}
        )
    for a in news.get("top_articles", []):
        evidence.append({"text": a["title"], "source": f"News: {a['source']}", "url": a["url"]})

    return {
        "category": category,
        "keyword": keyword,
        "score": round(min(max(score, 0.0), 1.0), 2),
        "viral_label": viral_label,
        "durability_label": durability_label,
        "breadth_score": round(breadth_score, 2),
        "volume_score": round(volume_score, 2),
        "diversity_score": round(diversity_score, 2),
        "durability_score": round(durability_score, 2),
        "insights": insights,
        "evidence": evidence[:3],
        "trends": trends,
        "youtube": youtube,
        "reddit": reddit,
        "news": news,
    }
