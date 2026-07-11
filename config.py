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
# HANYA lolos ke notifikasi kalau: (a) token genuine FRESH/baru (umurnya
# sendiri, dari candle_count GMGN -- BUKAN cuma "baru buat state kita",
# lihat catatan di bawah), atau (b) token LAMA yg run ini genuine mencetak
# ATH baru (dikonfirmasi GMGN, lihat state.update_ath()). Fokus sinyal ke
# token runner asli, bukan re-surface token lama yg cuma bouncing di bawah
# puncaknya (kasus nyata $NEIL/$SQUIRE).
#
# KENAPA "fresh" TAK BOLEH cuma pakai riwayat lokal kita (state_data.json):
# token bisa SUDAH LAMA ada di chain tp BARU PERTAMA KALI lolos filter kita
# (mis. baru migrasi ke Meteora, atau baru capai mcap/volume threshold) --
# kalau "fresh" diputuskan cuma dari "blm ada di state kita", token semacam
# itu salah lolos sbg "baru" walau sebenarnya sudah jauh dari ATH aslinya
# (persis pola $NEIL/$SQUIRE, cuma lewat jalur beda). candle_count dari
# GMGN (jumlah candle HARIAN yg ada = umur riil token dlm hari) dipakai sbg
# sinyal freshness UTAMA -- tak bisa "ditipu" oleh gap riwayat lokal kita.
ATH_GATE_FOR_KNOWN_TOKENS = _env_bool("ATH_GATE_FOR_KNOWN_TOKENS", True)
# Token dianggap "fresh" kalau candle harian GMGN <= ini (umur token dlm
# hari). GMGN candle mulai dihitung sejak token py transaksi pertama.
# Batas 1 (permintaan eksplisit user, 9 Juli 2026): usia <1 hari -> lolos
# tanpa perlu cetak ATH baru (asal hard gate lain lolos); usia >1 hari ->
# WAJIB cetak ATH baru buat lolos S2.5 (lihat pemakaian di main.py).
ATH_FRESH_TOKEN_MAX_CANDLES = _env_int("ATH_FRESH_TOKEN_MAX_CANDLES", 1)

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

# --- Discovery pool BARU (permintaan eksplisit user, 11 Juli 2026) ---
# Bug nyata: fetch_pools() default terurut volume_24h:desc -> pool baru
# (volume msh ~0) KALAH ranking TERUS drpd pool yg udah rame, tak PERNAH
# kebagian slot MAX_EXPENSIVE_CANDIDATES yg dibatasi (bukti log: $TRIPLET
# tak pernah muncul sepanjang ~35 jam). Fix: fetch KEDUA (terurut kebaruan,
# lihat meteora.fetch_newest_pools()) dgn slot CADANGAN sendiri di deep-check
# budget, supaya pool baru dpt kesempatan dicek terlepas dari serame apa
# pool lain saat itu. TIDAK melonggarkan gate keamanan/kualitas apa pun
# (MIN_CUMULATIVE_FEE_SOL/MIN_MARKET_CAP_USD dst tetap sama) -- cuma
# mastiin pool yg MEMENUHI gate itu tak kalah rebutan slot evaluasi.
MAX_NEWEST_POOLS_FETCH = _env_int("MAX_NEWEST_POOLS_FETCH", 100)
MAX_EXPENSIVE_CANDIDATES_NEWEST_RESERVED = _env_int("MAX_EXPENSIVE_CANDIDATES_NEWEST_RESERVED", 10)

# Anti-spam: interval minimal (jam) sebelum re-notif token yang sama pada verdict sama.
RENOTIFY_COOLDOWN_HOURS = _env_float("RENOTIFY_COOLDOWN_HOURS", 24.0)

# Dry run: proses & log tapi jangan kirim Telegram (buat testing lokal).
DRY_RUN = _env_bool("DRY_RUN", False)
# Kirim juga verdict SKIP ringkas untuk audit (default off, hemat noise).
SEND_SKIP_AUDIT = _env_bool("SEND_SKIP_AUDIT", False)

STATE_FILE = os.getenv("STATE_FILE", "state_data.json")

# Mint SOL untuk konversi harga.
SOL_MINT = "So11111111111111111111111111111111111111112"


# ---------------------------------------------------------------------------
# POSITION MONITOR — /start /stop /list /status (pantau LP yg SUDAH dipegang,
# beda dari pipeline di atas yg screening kandidat BARU). File state terpisah
# krn skema & siklus hidupnya beda total dari state_data.json (per-pool posisi
# aktif, bukan per-token riwayat harga screening).
# ---------------------------------------------------------------------------
MONITOR_STATE_FILE = os.getenv("MONITOR_STATE_FILE", "monitor_state.json")
MONITOR_DEFAULT_TRAIL_PCT = _env_float("MONITOR_DEFAULT_TRAIL_PCT", 15.0)

# Interval polling adaptif per pool (menit) -- makin baru posisi, makin sering
# dicek (risiko rug tertinggi di jam2 pertama). Cron sendiri tetap jalan tiap
# 5 menit (batas minimum GH Actions); interval di bawah ini dicapai dgn
# SKIP pool yg belum jatuh tempo cek-nya (lihat position_monitor.py), bukan
# dgn banyak jadwal cron terpisah.
MONITOR_POLL_MIN_UNDER_1H = _env_int("MONITOR_POLL_MIN_UNDER_1H", 5)
MONITOR_POLL_MIN_1H_TO_24H = _env_int("MONITOR_POLL_MIN_1H_TO_24H", 10)
MONITOR_POLL_MIN_OVER_24H = _env_int("MONITOR_POLL_MIN_OVER_24H", 15)
# Begitu ADA alert (fast/slow) dlm 1 jam terakhir -> balik ke interval
# tersering sampai stabil (tak ada trigger baru) 1 jam penuh.
MONITOR_POLL_MIN_AFTER_ALERT = _env_int("MONITOR_POLL_MIN_AFTER_ALERT", 5)
MONITOR_ALERT_COOLDOWN_STABLE_HOURS = _env_float("MONITOR_ALERT_COOLDOWN_STABLE_HOURS", 1.0)

# Trigger 1 (TVL trailing stop): butuh N cek BERTURUT di bawah stop_level
# sblm alert (redam noise) -- KECUALI pola fast-rug (lihat di bawah).
MONITOR_TVL_STOP_CONFIRM_CYCLES = _env_int("MONITOR_TVL_STOP_CONFIRM_CYCLES", 3)
# Trigger 2 (Vol/TVL collapse alone) & Trigger 3 (composite slow rug) --
# jg butuh konfirmasi N cek berturut, redam noise pola gradual.
MONITOR_VOLTVL_CONFIRM_CYCLES = _env_int("MONITOR_VOLTVL_CONFIRM_CYCLES", 3)
MONITOR_COMPOSITE_CONFIRM_CYCLES = _env_int("MONITOR_COMPOSITE_CONFIRM_CYCLES", 3)
# Vol/TVL dianggap "collapse" kalau rasio SEKARANG < ini x rata2 rolling-nya.
MONITOR_VOLTVL_COLLAPSE_RATIO = _env_float("MONITOR_VOLTVL_COLLAPSE_RATIO", 0.5)

# FAST RUG override (trigger 1): trail_percent KECIL (pool masih sangat baru,
# risiko tertinggi) + penurunan TAJAM dlm 1 SIKLUS -> alert LANGSUNG, skip
# konfirmasi 3-siklus (kalau nunggu, keburu rugi lebih dalam).
MONITOR_FAST_RUG_MAX_TRAIL_PCT = _env_float("MONITOR_FAST_RUG_MAX_TRAIL_PCT", 15.0)
MONITOR_FAST_RUG_SINGLE_CYCLE_DROP_PCT = _env_float("MONITOR_FAST_RUG_SINGLE_CYCLE_DROP_PCT", 20.0)

# Trigger 4 (range breach): kita HANYA punya pool_address (bukan alamat posisi
# NFT DLMM spesifik user), jadi "keluar range" diaproksimasi dari seberapa
# jauh harga bergerak dari entry_price -- BUKAN bin range asli user (perlu
# posisi NFT on-chain utk itu, di luar scope saat ini). Default 90% selaras
# filosofi LP pasif proyek ini sendiri (lihat docstring atas file: "range
# -90%, pasang-dan-lupakan") -- override per-pool via argumen ke-3 /start
# kalau strategi user beda dari default.
MONITOR_DEFAULT_RANGE_PCT = _env_float("MONITOR_DEFAULT_RANGE_PCT", 90.0)

# Anti-spam: jangan kirim ulang alert TYPE yg sama utk pool yg sama dlm N
# menit -- KECUALI CRITICAL (authority/LP integrity), itu SELALU kirim.
MONITOR_ALERT_DEDUP_MINUTES = _env_int("MONITOR_ALERT_DEDUP_MINUTES", 30)
