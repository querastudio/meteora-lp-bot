"""
config.py — Semua threshold & bobot scoring di satu tempat.

Filosofi user (LP pasif, bid-ask SOL-side, range -90%, pasang-dan-lupakan):
  - Keamanan kontrak = prioritas MUTLAK (satu rug = kehancuran, tak dijaga).
  - Cari volume TAHAN LAMA (bukan spike 1 jam) -> fee stabil selagi didiamkan.
  - Toleran penurunan bertahap, TIDAK toleran "mati vertikal ke nol".

Semua angka di sini boleh kamu tuning tanpa menyentuh logika di modul lain.
Nilai bisa dioverride lewat environment variable (lihat helper _env_* di bawah),
supaya bisa diatur dari GitHub Secrets/Variables tanpa edit kode.
"""

import os


# ---------------------------------------------------------------------------
# Helper baca env var dengan fallback (biar semua threshold bisa dioverride)
# ---------------------------------------------------------------------------
def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# SECRETS (via environment / GitHub Secrets) — JANGAN hardcode nilainya
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")  # opsional (Stage 7)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")     # opsional (sintesis narasi AI)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")         # opsional (fallback sintesis narasi AI)
LUNARCRUSH_API_KEY = os.getenv("LUNARCRUSH_API_KEY", "")  # opsional, BERBAYAR (~$24/bln)


# ---------------------------------------------------------------------------
# STAGE 1 — HARD FILTER POOL (dari data Meteora, 0 call tambahan)
# ---------------------------------------------------------------------------
MIN_TVL_USD = _env_float("MIN_TVL_USD", 1_000)           # likuiditas pool minimal
MIN_BASE_FEE_PCT = _env_float("MIN_BASE_FEE_PCT", 2.0)   # base fee >= 2% (fee gemuk)
MIN_BIN_STEP = _env_int("MIN_BIN_STEP", 100)             # bin step >= 100 (volatile-friendly)
MIN_CUMULATIVE_FEE_SOL = _env_float("MIN_CUMULATIVE_FEE_SOL", 20.0)  # total fee global >= 20 SOL
# Quote token yang diterima (mint address). Token dasar bukan quote.
QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112": "SOL",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
}


# ---------------------------------------------------------------------------
# STAGE 2 — HARD FILTER TOKEN (Dexscreener)
# ---------------------------------------------------------------------------
MIN_MARKET_CAP_USD = _env_float("MIN_MARKET_CAP_USD", 300_000)
# BUKAN hard gate lagi (dihapus dari hard_filters.stage2_token per keputusan
# user 8 Juli 2026 -- jadi bottleneck dominan funnel, gate "Volume Organik"
# proporsional di Stage 2.5 sudah gantikan perannya). Tetap dipakai
# screening/volatility.py sbg ambang soft-signal "volume tahan lama".
MIN_VOLUME_H24_USD = _env_float("MIN_VOLUME_H24_USD", 1_000_000)

# --- Volume ORGANIK & TINGGI (per rumus user) ---
# Rasio sehat mcap:global_sol_fee ~ 10.000:1 (mcap $100k <-> fee kumulatif
# global 10 SOL) -- proxy "fee yg terkumpul sepadan dgn ukuran mcap-nya",
# BEDA dari MIN_CUMULATIVE_FEE_SOL (Stage 1, floor FLAT 20 SOL tanpa peduli
# mcap -- token mcap $2jt yg cuma kumpul 20 SOL fee jelas timpang, tapi flat
# floor tak pernah nangkep itu). Gate INI proporsional thdp mcap, jalan di
# Stage 2 (butuh mcap dari Dexscreener, blm ada di Stage 1).
MCAP_TO_FEE_SOL_RATIO = _env_float("MCAP_TO_FEE_SOL_RATIO", 10_000.0)
# HARD GATE atau cuma soft warning -- default HARD (selaras filosofi "volume
# organik & tinggi" sbg syarat inti, bukan sekadar nice-to-have).
VOLUME_ORGANIC_HARD_GATE = _env_bool("VOLUME_ORGANIC_HARD_GATE", True)
# --- ATH gate utk token LAMA (permintaan eksplisit user, 8 Juli 2026) ---
# Token yg SUDAH py riwayat ATH tercatat sblmnya ("dikenal") HANYA lolos ke
# notifikasi kalau run ini genuine mencetak ATH baru (dikonfirmasi GMGN --
# lihat state.update_ath()) -- fokus sinyal ke breakout asli, bukan
# re-surface token lama yg cuma bouncing di bawah puncaknya (kasus nyata
# $NEIL/$SQUIRE). Token BARU (blm py riwayat "ath" sblm run ini) TETAP
# lolos apa adanya -- "baru pertama kali kelihatan" itu sendiri sudah
# informasi berharga, tak ada "rekor lama" utk dibandingkan.
ATH_GATE_FOR_KNOWN_TOKENS = _env_bool("ATH_GATE_FOR_KNOWN_TOKENS", True)

# Toleransi: jangan gugurkan tepat di garis, kasih buffer (rasio boleh SEDIKIT
# di atas target sblm dianggap gagal) -- data fee on-chain naturally noisy.
MCAP_TO_FEE_SOL_TOLERANCE = _env_float("MCAP_TO_FEE_SOL_TOLERANCE", 1.5)  # 1.5x buffer

# Ambang volume 5 menit (dari GMGN volume_momentum) utk label "tinggi" --
# ditampilkan + kontribusi soft-score, BUKAN hard gate (byk runner asli msh
# di bawah ini pas awal sekali, jangan buang kandidat legit krn ini).
VOLUME_5M_HIGH_USD = _env_float("VOLUME_5M_HIGH_USD", 50_000.0)
VOLUME_5M_DECENT_USD = _env_float("VOLUME_5M_DECENT_USD", 10_000.0)


# ---------------------------------------------------------------------------
# STAGE 3 — KEAMANAN KONTRAK (Helius) — PALING KRITIS
# ---------------------------------------------------------------------------
# Transfer-fee Token-2022 maksimal yang ditoleransi (basis poin). 0 = tanpa tax.
# Tax tinggi = honeypot terselubung, menggerogoti fee LP tiap swap.
MAX_TRANSFER_FEE_BPS = _env_int("MAX_TRANSFER_FEE_BPS", 50)  # 50 bps = 0.5%


# ---------------------------------------------------------------------------
# STAGE 4 — DISTRIBUSI HOLDER (Helius)
# ---------------------------------------------------------------------------
MAX_TOP10_SUPPLY_PCT = _env_float("MAX_TOP10_SUPPLY_PCT", 30.0)  # HARD GATE
TOP_N_HOLDERS_FETCH = _env_int("TOP_N_HOLDERS_FETCH", 100)       # ambil top 100
TOP_N_HOLDERS_INSPECT = _env_int("TOP_N_HOLDERS_INSPECT", 20)    # cek heuristik top 20
# Heuristik "wallet aneh"
FRESH_WALLET_MAX_TXS = _env_int("FRESH_WALLET_MAX_TXS", 10)      # tx sangat sedikit
EMPTY_WALLET_SOL_USD = _env_float("EMPTY_WALLET_SOL_USD", 100.0) # saldo SOL < $100
# Proporsi wallet mencurigakan di top20 yang bikin skor turun / SKIP
SUSPICIOUS_TOP20_PCT_THRESHOLD = _env_float("SUSPICIOUS_TOP20_PCT_THRESHOLD", 30.0)

# Wallet dianggap "muda" bila umurnya (sejak tx pertama terlihat) di bawah ini.
# Dipakai bareng fresh/empty utk sinyal "coordinated trading" -- lihat holders.py.
WALLET_YOUNG_AGE_HOURS = _env_float("WALLET_YOUNG_AGE_HOURS", 24.0)
# Ambang rata-rata (fresh%, empty%, young%) top20 utk label indikasi bundling/
# wash trading. Makin tinggi & seragam ketiganya -> makin kuat indikasinya.
COORDINATION_HIGH_PCT = _env_float("COORDINATION_HIGH_PCT", 70.0)
COORDINATION_MED_PCT = _env_float("COORDINATION_MED_PCT", 40.0)

# --- Deteksi CLUSTER/BUNDLE (ala GMGN/DevsNightmare, versi gratis) ---
# Proxy: wallet top holder yang "lahir" (tx pertama terlihat) dalam jendela
# waktu sempit satu sama lain -> kemungkinan 1 entitas pakai banyak wallet
# (bundler/sniper terkoordinasi). TANPA API call tambahan (pakai data yg sudah
# diambil utk fresh-wallet check). Bukan exact funding-source match spt GMGN
# (yang trace siapa danai wallet) -- tapi cukup menangkap pola paling umum:
# banyak wallet baru dibuat berdekatan sesaat sebelum/saat token diluncurkan.
CLUSTER_TIME_WINDOW_SECONDS = _env_int("CLUSTER_TIME_WINDOW_SECONDS", 600)  # 10 menit
# HARD GATE: bundler/cluster boleh ADA, asal tak kuasai mayoritas supply.
MAX_CLUSTER_SUPPLY_PCT = _env_float("MAX_CLUSTER_SUPPLY_PCT", 25.0)


# ---------------------------------------------------------------------------
# STAGE 5 — METRIK KUALITAS LP
# ---------------------------------------------------------------------------
# Target yield fee harian riil (fee_24h / TVL). Inti profitabilitas LP pasif.
FEE_TVL_DAILY_GOOD_PCT = _env_float("FEE_TVL_DAILY_GOOD_PCT", 3.0)   # >=3% harian bagus
FEE_TVL_DAILY_GREAT_PCT = _env_float("FEE_TVL_DAILY_GREAT_PCT", 6.0) # >=6% sangat bagus
VOL_TVL_GOOD_RATIO = _env_float("VOL_TVL_GOOD_RATIO", 2.0)           # velocity target 2-3x
VOL_TVL_GREAT_RATIO = _env_float("VOL_TVL_GREAT_RATIO", 3.0)
# Umur pool: prefer yang sudah lewat fase peluncuran liar (kurangi risiko sniper dump).
POOL_MIN_AGE_HOURS_HEALTHY = _env_float("POOL_MIN_AGE_HOURS_HEALTHY", 24.0)


# ---------------------------------------------------------------------------
# STAGE 6 — VOLATILITAS "TURUN STABIL"
# ---------------------------------------------------------------------------
# Drawdown 24h yang dianggap "mati vertikal" (SKIP walau sempat ATH).
VERTICAL_DEATH_DRAWDOWN_PCT = _env_float("VERTICAL_DEATH_DRAWDOWN_PCT", 60.0)
# Minimal jumlah "hari volume tahan" (proxy dari h6/h24 Dexscreener) untuk skor tinggi.
# Kita approx pakai konsistensi volume antar-window (lihat volatility.py).

# --- Momentum VWAP (opsional, gratis via GeckoTerminal OHLCV) ---
# Harga sekarang vs VWAP sejak pool dibuat -- ala indikator "VWAP hlc3
# Century" yang user pantau manual di GMGN. SOFT SCORE saja (bukan hard
# gate) -- lihat sources/geckoterminal.py utk alasan & kurva skornya.
VWAP_MOMENTUM_ENABLED = _env_bool("VWAP_MOMENTUM_ENABLED", True)


# ---------------------------------------------------------------------------
# STAGE 7 — VALIDASI NARASI VIRAL (kualitatif + kuantitatif, lintas platform)
# Dipecah 2 sumbu: VIRALITAS (breadth+volume+diversitas komunitas) vs DAYA
# TAHAN (masih hidup beberapa hari, bukan cuma spike sesaat). Lihat
# sources/narrative.py utk detail formula.
# ---------------------------------------------------------------------------
NARRATIVE_ENABLED = _env_bool("NARRATIVE_ENABLED", True)
YOUTUBE_LOOKBACK_HOURS = _env_int("YOUTUBE_LOOKBACK_HOURS", 72)
GOOGLE_TRENDS_TIMEFRAME = os.getenv("GOOGLE_TRENDS_TIMEFRAME", "now 7-d")

# Reddit (gratis, no key) -- endpoint publik search.json, TIDAK resmi
# didokumentasikan utk otomasi berat. DEFAULT OFF: dikonfirmasi live berulang
# kali (semua run sesi ini) SELALU balas 403 (Cloudflare block), 0% berhasil,
# 0% kontribusi sinyal -- murni buang waktu tiap kandidat. Selaras jg dgn
# permintaan user "narasi sederhanakan pakai data google trends, pumpfun
# coin community" (2 kanal utama). Set True lagi kalau endpoint-nya jalan lg.
REDDIT_ENABLED = _env_bool("REDDIT_ENABLED", False)

# Ambang "aktif" per platform utk hitung BREADTH (berapa dari 4 platform hidup).
NARRATIVE_MIN_YOUTUBE_VIDEOS = _env_int("NARRATIVE_MIN_YOUTUBE_VIDEOS", 5)
NARRATIVE_MIN_REDDIT_POSTS = _env_int("NARRATIVE_MIN_REDDIT_POSTS", 3)
NARRATIVE_MIN_NEWS_ARTICLES = _env_int("NARRATIVE_MIN_NEWS_ARTICLES", 2)

# Cap normalisasi VOLUME (angka mentah -> skor 0-1; cap lembut biar 1 metrik
# whale tak mendominasi skor).
NARRATIVE_YOUTUBE_VIEWS_CAP = _env_float("NARRATIVE_YOUTUBE_VIEWS_CAP", 1_000_000)
NARRATIVE_REDDIT_SCORE_CAP = _env_float("NARRATIVE_REDDIT_SCORE_CAP", 5_000)
NARRATIVE_NEWS_ARTICLES_CAP = _env_float("NARRATIVE_NEWS_ARTICLES_CAP", 20)

# Cap normalisasi DIVERSITAS KOMUNITAS (proxy kualitatif "banyak komunitas").
NARRATIVE_REDDIT_SUBREDDIT_CAP = _env_float("NARRATIVE_REDDIT_SUBREDDIT_CAP", 10)
NARRATIVE_YOUTUBE_CHANNEL_CAP = _env_float("NARRATIVE_YOUTUBE_CHANNEL_CAP", 10)
NARRATIVE_NEWS_DOMAIN_CAP = _env_float("NARRATIVE_NEWS_DOMAIN_CAP", 8)

# --- Pump.fun Community chat (opsional, API key via coin-communities.xyz) ---
# DIKONFIRMASI USER: ini backend ASLI fitur community/chat di halaman token
# pump.fun (dilihat langsung dari network request pump.fun) -- setiap token
# pump.fun otomatis punya community di sini, coverage jauh lebih tinggi drpd
# Reddit/YouTube utk token BARU. Lihat sources/pumpfun_community.py.
PUMPFUN_COMMUNITY_API_KEY = os.getenv("PUMPFUN_COMMUNITY_API_KEY", "")
# Server API key pair (BEDA dari API key biasa di atas) -- dikonfirmasi live
# (7 Juli 2026): getMessages/getCommunityMembers pakai x-api-key biasa balik
# 401 walau community lookup (x-api-key sama) sukses. Docs SDK sebut endpoint
# */Server (getMessagesServer, getCommunityMembersServer) yg didesain khusus
# utk backend/bot (baca tanpa sesi login user) pakai kredensial TERPISAH:
# x-server-key + x-server-secret. Bikin dari dashboard coincommunities.org ->
# menu "Server API keys" (BEDA dari "API keys" biasa).
PUMPFUN_COMMUNITY_SERVER_KEY = os.getenv("PUMPFUN_COMMUNITY_SERVER_KEY", "")
PUMPFUN_COMMUNITY_SERVER_SECRET = os.getenv("PUMPFUN_COMMUNITY_SERVER_SECRET", "")
PUMPFUN_COMMUNITY_ENABLED = _env_bool("PUMPFUN_COMMUNITY_ENABLED", True)
NARRATIVE_MIN_PUMPFUN_POSTS = _env_int("NARRATIVE_MIN_PUMPFUN_POSTS", 5)
NARRATIVE_PUMPFUN_LIKES_CAP = _env_float("NARRATIVE_PUMPFUN_LIKES_CAP", 500)
# Platform RESMI launchpad pump.fun (bukan sekadar kanal text-search generik
# spt Reddit/YouTube/News) -- diprioritaskan sbg BASE narasi via bobot lebih
# tinggi di breadth/volume/durability (lihat narrative.py). Token
# well-established (community sudah ramai lama) otomatis unggul di sini duluan.
NARRATIVE_PUMPFUN_PRIORITY_WEIGHT = _env_float("NARRATIVE_PUMPFUN_PRIORITY_WEIGHT", 2.0)

# --- Sintesis narasi via Gemini API gratis (opsional, HANYA soft-nudge) ---
# Klasifikasi organik/campuran/terkoordinasi dari kutipan Reddit/News yang
# sudah lolos filter relevansi. Skor dikalikan (0.6-1.0), TIDAK additif,
# dan TIDAK pernah menyentuh hard gate keamanan/holder. Lihat sources/gemini.py.
GEMINI_NARRATIVE_ENABLED = _env_bool("GEMINI_NARRATIVE_ENABLED", True)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# --- Fallback ke Groq API gratis kalau Gemini gagal/kena limit harian ---
# Rate limit gratis Groq jauh lebih longgar drpd Gemini -- lihat sources/groq.py.
GROQ_NARRATIVE_ENABLED = _env_bool("GROQ_NARRATIVE_ENABLED", True)
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# --- Gate bukti minimum sebelum panggil AI narasi ---
# Kalau Reddit & News keduanya terlalu tipis (mis. cuma 1-2 artikel generik
# yang bahkan bisa salah topik -- Google News RSS kadang match longgar),
# LEBIH BAIK skip AI sepenuhnya drpd memaksa dia menilai organik/terkoordinasi
# dari data hampir kosong (kasus nyata: $HeavyPulp divonis "terkoordinasi"
# padahal News-nya cuma 2 artikel generik, salah satunya malah soal token
# lain). Threshold ini SENGAJA beda (lebih ketat) drpd NARRATIVE_MIN_*
# (yang dipakai utk breadth_score, tujuannya beda -- itu ukur "seberapa
# ramai", ini ukur "cukup bersih utk diminta opini AI").
AI_MIN_REDDIT_POSTS = _env_int("AI_MIN_REDDIT_POSTS", 2)
AI_MIN_NEWS_ARTICLES = _env_int("AI_MIN_NEWS_ARTICLES", 3)
# Chat pump.fun -- gate TERPISAH (OR dgn 2 di atas), krn bukti dari komunitas
# resmi platform sendiri sudah cukup jadi alasan panggil AI walau Reddit/News
# masih tipis (token baru wajar blm sempat viral di sana).
AI_MIN_PUMPFUN_POSTS = _env_int("AI_MIN_PUMPFUN_POSTS", 3)

# --- LunarCrush (opsional, BERBAYAR ~$24/bln) -- Galaxy Score/sentiment X ---
# TIDAK meng-index token super baru (dikonfirmasi manual) -- wajar 404 utk
# kandidat fresh, degrade gracefully. Lihat sources/lunarcrush.py.
LUNARCRUSH_ENABLED = _env_bool("LUNARCRUSH_ENABLED", True)

# --- Jupiter Organic Score (gratis, no key) -- legitimasi volume asli ---
# BUKAN sinyal narasi -- ini deteksi wash-trading/bot-volume, penguat utk
# Stage 4/6. Lihat sources/jupiter.py.
JUPITER_ORGANIC_ENABLED = _env_bool("JUPITER_ORGANIC_ENABLED", True)

# --- GMGN OpenAPI (gratis, apply via https://gmgn.ai/ai) -- penguat due
# diligence: token/security (honeypot/tax/rug_ratio, cross-check Helius),
# token/info (dev holding %), market/token_top_holders (tag wallet
# smart_degen/bundler/sniper/rat_trader -- funding-source tracing ASLI,
# bukan proxy waktu spt cluster-detection kita di holders.py).
# INFORMASIONAL SAJA (tampil di notif sbg konteks tambahan) -- BUKAN hard
# gate baru (hard gate keamanan/holder tetap otoritatif dari Helius) dan
# TIDAK menyentuh skor. Lihat sources/gmgn.py.
GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
GMGN_ENABLED = _env_bool("GMGN_ENABLED", True)


# ---------------------------------------------------------------------------
# SCORING ENGINE — BOBOT SOFT SCORE (total mencerminkan profil pasif-konservatif)
# Keamanan sudah jadi HARD GATE (biner). Soft score menimbang kualitas/keberlanjutan.
# Bobot terbesar: fee/TVL & volume-tahan-lama (fee stabil) dan holder health.
# ---------------------------------------------------------------------------
WEIGHTS = {
    # Stage 5 — kualitas LP (inti cuan pasif)
    "fee_tvl": _env_float("W_FEE_TVL", 25.0),          # yield fee harian riil
    "vol_tvl": _env_float("W_VOL_TVL", 15.0),          # velocity
    "lp_concentration": _env_float("W_LP_CONC", 10.0), # risiko LP dominan tarik likuiditas
    "pool_age": _env_float("W_POOL_AGE", 5.0),         # sudah lewat fase liar
    # Stage 6 — volatilitas turun-stabil (keberlanjutan fee di range -90%)
    "volatility": _env_float("W_VOLATILITY", 20.0),
    # Stage 4 — kesehatan holder (soft bagian, di luar hard gate top10)
    "holder_health": _env_float("W_HOLDER", 15.0),
    # Stage 7 — narasi tahan lama (volume tahan lama sering ditopang narasi hidup)
    "narrative": _env_float("W_NARRATIVE", 10.0),
    # Momentum VWAP (opsional) — sinyal timing/hype, bukan keamanan/fee inti,
    # jadi bobotnya kecil & degrade ke netral (0.5) kalau data tak tersedia.
    "vwap_momentum": _env_float("W_VWAP_MOMENTUM", 8.0),
    # LunarCrush Galaxy Score (opsional, berbayar) — sinyal sosial X asli.
    # Nyaris selalu netral (0.5) utk token super baru (belum ter-index),
    # jadi bobotnya kecil spy tak mendominasi skor saat n/a.
    "lunarcrush_social": _env_float("W_LUNARCRUSH", 8.0),
    # Jupiter Organic Score (gratis) — penguat deteksi wash-trading, di
    # samping heuristik holder/volatilitas yg sudah ada.
    "jupiter_organic": _env_float("W_JUPITER_ORGANIC", 10.0),
    # Volume ORGANIK & TINGGI (rasio mcap:fee + volume 5m GMGN) — permintaan
    # eksplisit user, bobot cukup besar krn ini salah satu dari 4 pilar inti
    # yg diminta ("volume tinggi & organik").
    "volume_organic": _env_float("W_VOLUME_ORGANIC", 12.0),
    # ATH momentum — harga BARU SAJA mencetak rekor tertinggi (bukan cuma
    # naik dari kemarin) = sinyal minat beli asli/genuine breakout, beda dari
    # "volatility" (yg menilai drawdown/stabilitas, bukan arah rekor baru).
    "ath_momentum": _env_float("W_ATH_MOMENTUM", 6.0),
}
# Total bobot dinormalisasi otomatis di scoring.py (skor akhir tetap 0-100
# walau total di atas tak persis 100).

# Ambang verdict berdasarkan soft score (0-100) SETELAH semua hard gate lolos.
VERDICT_STRONG_MIN = _env_float("VERDICT_STRONG_MIN", 65.0)
VERDICT_WATCH_MIN = _env_float("VERDICT_WATCH_MIN", 40.0)
# < WATCH_MIN => tetap kirim sebagai WATCH lemah? Tidak: di bawah ini tak dikirim.

# Kalau ada ⚠️ (mis. LP-lock tak terverifikasi) walau skor tinggi -> turunkan ke WATCH.
DOWNGRADE_STRONG_TO_WATCH_ON_WARNING = _env_bool("DOWNGRADE_ON_WARN", True)


# ---------------------------------------------------------------------------
# OPERASIONAL / RATE LIMIT
# ---------------------------------------------------------------------------
HTTP_TIMEOUT = _env_int("HTTP_TIMEOUT", 20)
HTTP_MAX_RETRIES = _env_int("HTTP_MAX_RETRIES", 3)
HTTP_BACKOFF_BASE = _env_float("HTTP_BACKOFF_BASE", 1.5)  # detik
# Batasi jumlah pool yang diproses per run agar cron 5 menit selalu selesai cepat.
# Pool diambil terurut volume_24h:desc (lihat sources/meteora.py) -- makin besar
# angka ini, makin dalam funnel menjangkau pool BARU (mcap ok tapi volume 24h
# belum sempat mengejar pool lama). Stage 1 sendiri gratis (tanpa call
# tambahan), jadi menaikkan ini murah; yang mahal (Helius) tetap dibatasi via
# MAX_EXPENSIVE_CANDIDATES di bawah.
MAX_POOLS_PER_RUN = _env_int("MAX_POOLS_PER_RUN", 300)
# Batasi kandidat yang lolos Stage 1-2 masuk ke stage mahal (Helius) per run.
# Naik dari 15 -> 25 (permintaan user, 8 Juli 2026): Stage 1 rutin meloloskan
# ~40 pool/run tp cuma 15 teratas yg PERNAH dievaluasi lanjut -- pool
# peringkat 16-40 tak pernah dicek sama sekali, bukan gugur krn gate, tapi
# memang tak kebagian giliran. Trade-off: run makin lama (~4-5 menit di 15
# kandidat, makin banyak makin lama & makin sering nyenggol rate-limit
# Helius -- lihat sources/http.py penalti adaptif) -- msh aman di bawah
# timeout 10 menit workflow, tp kalau makin lambat lg, turunkan via env var
# ini (tak perlu ubah kode).
MAX_EXPENSIVE_CANDIDATES = _env_int("MAX_EXPENSIVE_CANDIDATES", 25)
# Anti-spam: interval minimal (jam) sebelum re-notif token yang sama pada verdict sama.
RENOTIFY_COOLDOWN_HOURS = _env_float("RENOTIFY_COOLDOWN_HOURS", 24.0)

# Dry run: proses & log tapi jangan kirim Telegram (buat testing lokal).
DRY_RUN = _env_bool("DRY_RUN", False)
# Kirim juga verdict SKIP ringkas untuk audit (default off, hemat noise).
SEND_SKIP_AUDIT = _env_bool("SEND_SKIP_AUDIT", False)

STATE_FILE = os.getenv("STATE_FILE", "state_data.json")

# Mint SOL untuk konversi harga.
SOL_MINT = "So11111111111111111111111111111111111111112"
