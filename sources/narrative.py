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
from sources import http, pumpfun_community

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
    # "Italian brainrot"/Gen-Z internet meme family (2024-2026 wave) --
    # ditambah krn ketahuan meleset di kasus nyata $TripleT ("Tung Tung
    # Tung Sahur", meme brainrot viral lintas platform tapi ticker-nya tak
    # mengandung kata kunci apa pun dari meme aslinya, jadi lolos sbg
    # "unknown" tanpa pattern ini).
    ("brainrot", r"\b(tung|sahur|skibidi|rizz|sigma|gyat|fanum|ohio|brainrot|bombardiro|tralalero)\b"),
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


def _pick_keyword(name: str, symbol: str) -> str:
    """
    Pilih kata kunci pencarian narasi (Trends/YouTube/Reddit/News).

    Kasus nyata yg ketahuan salah (dilaporkan user, token $TripleT): nama
    ASLI on-chain-nya "Tung Tung Tung Sahur" -- meme "Italian brainrot"
    yg viral lintas platform (X/TikTok/YouTube/IG/FB, dipakai banyak
    akun besar) -- tapi logic lama SELALU pakai symbol (ticker singkat,
    "TripleT") drpd name kalau symbol >=3 karakter. "TripleT" TAK ADA
    HUBUNGANNYA dgn frasa yg orang cari beneran, jadi Trends/YouTube/
    Reddit/News keliru kelihatan sepi -- token dinilai "SEDANG/SESAAT"
    padahal aslinya sangat viral, cuma krn search keyword-nya salah.

    Fix: nama on-chain MULTI-KATA (>=2 kata) yg beda dari symbol lebih
    mungkin FRASA asli yg orang cari (spt "Tung Tung Tung Sahur",
    "dogwifhat", dst) drpd ticker singkat -- prioritaskan itu. Kalau name
    cuma 1 kata / sama dgn symbol / kosong, symbol (kalau valid) tetap
    dipakai spt sebelumnya (banyak meme SATU KATA emang persis symbol-nya,
    mis. $PEPE/$WIF/$BONK).
    """
    name = (name or "").strip()
    symbol = (symbol or "").strip()
    if len(name.split()) >= 2 and name.lower() != symbol.lower():
        return name
    if symbol and len(symbol) >= 3 and symbol != "?":
        return symbol
    return name


def evaluate_narrative(name: str, symbol: str, mint: str = "") -> Dict[str, Any]:
    """
    Gabungkan semua sinyal jadi dua sumbu terpisah + rincian kuantitatif mentah
    (dipakai notify.py utk tampilkan angka asli, bukan cuma label).

    mint (opsional) -- token_address on-chain, dipakai utk cek chat komunitas
    pump.fun (lihat sources/pumpfun_community.py) -- kanal ke-5 selain
    Trends/YouTube/Reddit/News, di-key by mint (bukan text search) jadi tak
    butuh filter relevansi ticker-collision.

    Return:
      {
        category, keyword, score (0-1, dipakai scoring.py),
        viral_label ("SANGAT VIRAL"/"VIRAL"/"SEDANG"/"LEMAH"),
        durability_label ("TAHAN LAMA"/"SEDANG"/"SESAAT"),
        breadth_score, volume_score, diversity_score, durability_score (0-1),
        insights: [str, ...]  -- kalimat kualitatif otomatis (rule-based),
        trends, youtube, reddit, news, pumpfun  -- data mentah per sumber,
      }
    """
    empty = {
        "category": "n/a", "keyword": symbol, "score": 0.0,
        "viral_label": "OFF", "durability_label": "OFF",
        "breadth_score": 0.0, "volume_score": 0.0, "diversity_score": 0.0,
        "durability_score": 0.0, "insights": [], "evidence": [],
        "trends": {}, "youtube": {}, "reddit": {}, "news": {}, "pumpfun": {},
    }
    if not config.NARRATIVE_ENABLED:
        return empty

    keyword = _pick_keyword(name, symbol)
    category = detect_category(name, symbol)

    trends = google_trends_signal(keyword)
    youtube = youtube_signal(keyword)
    reddit = reddit_signal(keyword)
    news = google_news_signal(keyword)
    pumpfun = pumpfun_community.community_signal(mint)

    # --- BREADTH: berapa dari 5 platform yang menunjukkan aktivitas nyata ---
    # pump.fun community BUKAN cuma "kanal ke-5" setara -- ini platform resmi
    # launchpad-nya sendiri (alternatif X Community, dikonfirmasi user dari
    # network request pump.fun langsung), jadi diprioritaskan sbg BASE narasi
    # lewat bobot lebih tinggi (config.NARRATIVE_PUMPFUN_PRIORITY_WEIGHT,
    # default 2x) drpd kanal generik (Trends/YouTube/Reddit/News yg cuma
    # text-search, bisa nyasar/noise). Token WELL-ESTABLISHED (community
    # sudah ramai lama) otomatis dpt breadth/volume tinggi dari sini duluan;
    # token BARU yg dev/komunitasnya blm sempat buat community sama sekali
    # cuma dpt available=False -- degrade netral spt kanal lain, TIDAK
    # dihukum (lihat channels_blind di bawah).
    pf_weight = config.NARRATIVE_PUMPFUN_PRIORITY_WEIGHT
    flag_weights = [
        (bool(trends.get("available") and trends.get("rising")), 1.0),
        (bool(youtube.get("available") and youtube.get("video_count", 0) >= config.NARRATIVE_MIN_YOUTUBE_VIDEOS), 1.0),
        (bool(reddit.get("available") and reddit.get("post_count", 0) >= config.NARRATIVE_MIN_REDDIT_POSTS), 1.0),
        (bool(news.get("available") and news.get("article_count", 0) >= config.NARRATIVE_MIN_NEWS_ARTICLES), 1.0),
        (bool(pumpfun.get("available") and pumpfun.get("post_count", 0) >= config.NARRATIVE_MIN_PUMPFUN_POSTS), pf_weight),
    ]
    active_flags = [f for f, _ in flag_weights]  # dipakai insight "aktif di N/5 platform"
    total_weight = sum(w for _, w in flag_weights)
    breadth_score = sum(w for f, w in flag_weights if f) / total_weight

    # --- VOLUME: angka mentah dinormalisasi (kuantitatif), pump.fun bobot 2x ---
    vol_parts: List[tuple] = []
    if trends.get("available"):
        vol_parts.append((_norm(trends.get("avg", 0.0), 100.0), 1.0))
    if youtube.get("available"):
        vol_parts.append((_norm(youtube.get("total_views", 0), config.NARRATIVE_YOUTUBE_VIEWS_CAP), 1.0))
    if reddit.get("available") and reddit.get("post_count", 0) > 0:
        vol_parts.append((_norm(reddit.get("total_score", 0), config.NARRATIVE_REDDIT_SCORE_CAP), 1.0))
    if news.get("available"):
        vol_parts.append((_norm(news.get("article_count", 0), config.NARRATIVE_NEWS_ARTICLES_CAP), 1.0))
    if pumpfun.get("available") and pumpfun.get("post_count", 0) > 0:
        vol_parts.append((_norm(pumpfun.get("total_likes", 0), config.NARRATIVE_PUMPFUN_LIKES_CAP), pf_weight))
    volume_score = (
        sum(v * w for v, w in vol_parts) / sum(w for _, w in vol_parts) if vol_parts else 0.0
    )

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
    # (pump.fun jg dpt bobot 2x, konsisten dgn breadth/volume di atas)
    dur_parts: List[tuple] = []
    if trends.get("available"):
        dur_parts.append((1.0 if trends.get("sustained") else 0.2, 1.0))
    if reddit.get("available") and reddit.get("post_count", 0) > 0:
        # masih ada post BARU 24 jam terakhir dalam window 7 hari -> msh hidup.
        dur_parts.append((1.0 if reddit.get("posts_last24h", 0) > 0 else 0.3, 1.0))
    if pumpfun.get("available") and pumpfun.get("post_count", 0) > 0:
        dur_parts.append((1.0 if pumpfun.get("posts_last24h", 0) > 0 else 0.3, pf_weight))
    durability_score = (
        sum(v * w for v, w in dur_parts) / sum(w for _, w in dur_parts) if dur_parts else 0.0
    )

    # --- Skor komposit (dipakai scoring.py, bobot "narrative") ---
    score = (
        0.30 * breadth_score
        + 0.30 * volume_score
        + 0.15 * diversity_score
        + 0.25 * durability_score
    )

    # --- Buta-kanal: Reddit/YouTube/News/pump.fun nihil BUKAN bukti "tak ada
    # narasi". X/Twitter -- kanal UTAMA hype memecoin Solana (lihat docstring
    # modul ini) -- tak bisa dicek otomatis sama sekali (no API gratis/aman,
    # sudah diriset tuntas). Kalau KEEMPAT kanal yg KITA pantau nihil, itu
    # cuma berarti kita buta di sini, bukan token-nya sepi -- jangan hukum
    # skor jatuh ke LEMAH krn itu, netralkan spt komponen soft-score lain
    # (VWAP/LunarCrush/Jupiter) yg default netral saat data tak tersedia.
    # (Trends dikecualikan dari cek ini -- kata umum spt "world"/"chance"
    # selalu ada baseline volume tak berkaitan, jadi bukan sinyal andal soal
    # buta/tidaknya kanal lain.)
    #
    # pump.fun community MENGECILKAN kejadian ini scr signifikan drpd
    # sebelumnya (cuma Reddit/YouTube/News): token yg baru migrasi ke
    # Meteora biasanya blm sempat viral di Reddit/YouTube/News, tapi chat
    # komunitas pump.fun-nya sendiri (kanal PALING relevan, langsung di
    # halaman token) sudah aktif sejak awal -- jadi lebih sering skor asli
    # (bukan floor netral) yg terpakai, persis yg diminta user.
    channels_blind = (
        reddit.get("post_count", 0) == 0
        and youtube.get("video_count", 0) == 0
        and news.get("article_count", 0) == 0
        and pumpfun.get("post_count", 0) == 0
    )
    if channels_blind:
        score = max(score, 0.5)

    # --- Label ---
    if channels_blind:
        viral_label = "❔ TAK TERUKUR (Reddit/YouTube/News/Pump.fun nihil -- cek manual X)"
    elif breadth_score >= 0.75 and volume_score >= 0.6:
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
    if channels_blind:
        insights.append(
            "Reddit/YouTube/News/Pump.fun nihil -- BUKAN bukti token ini sepi narasi, "
            "cuma kanal ini yg buta; X sering jadi kanal utama hype memecoin -- WAJIB cek manual"
        )
    n_active = sum(1 for f in active_flags if f)
    if n_active >= 3:
        insights.append(f"aktif di {n_active}/{len(active_flags)} platform (bukan 1 sumber saja)")
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

    if pumpfun.get("available") and pumpfun.get("post_count", 0) > 0:
        spam_pct = pumpfun.get("spam_count", 0) / pumpfun.get("post_count", 1) * 100.0
        if spam_pct >= 40:
            insights.append(
                f"chat pump.fun {spam_pct:.0f}% pesan ditandai spam -- kemungkinan botspam, bukan diskusi organik"
            )
        elif pumpfun.get("posts_last24h", 0) == 0:
            insights.append("chat pump.fun: tak ada pesan baru 24 jam terakhir -- momentum mereda")

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
    for p in pumpfun.get("top_posts", []):
        evidence.append(
            {"text": p["text"], "source": f"Chat pump.fun @{p['username']} ({p['likeCount']} like)", "url": ""}
        )

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
        "pumpfun": pumpfun,
    }
