# 🤖 Meteora DLMM — Bot Screening & Due Diligence LP

Bot notifikasi Telegram untuk **Liquidity Provider (LP) pasif di Meteora DLMM (Solana)**.
Setiap ~5 menit bot memindai pool & token baru, menjalankan due-diligence otomatis
(keamanan kontrak, distribusi holder, kualitas fee, volatilitas, narasi), lalu
mengirim **verdict 🟢 STRONG / 🟡 WATCH / 🔴 SKIP** ke Telegram beserta alasannya.

Dirancang untuk profil: **bid-ask, SOL-side, wide range hingga -90%, pasang-dan-lupakan.**
Karena posisi tak dijaga → **keamanan kontrak adalah prioritas mutlak**, dan yang
dicari adalah **volume tahan lama** (fee stabil), bukan spike sesaat.

> ⚠️ **Bukan nasihat finansial.** Bot ini alat bantu screening. Selalu lakukan
> verifikasi manual lewat link yang disediakan sebelum menaruh dana.

---

## 🚀 Cara Kerja (pipeline cascade: murah → mahal)

Token digugurkan sedini mungkin untuk hemat rate limit API gratis.

| Stage | Nama | Sumber | Tipe |
|------|------|--------|------|
| 1 | Hard filter pool (TVL, base fee, bin step, fee global 20 SOL, quote SOL/USDC) | Meteora | Hard gate |
| 2 | Hard filter token (mcap, vol24h, **ATH proximity**) | Dexscreener+GeckoTerminal | Hard gate |
| 3 | **Keamanan kontrak** (no-mint, no-freeze, no-tax) | Helius | Hard gate |
| 4 | Distribusi holder (top10 <30% = gate; fresh/empty wallet = soft) | Helius | Gate + soft |
| 5 | Kualitas LP (**fee/TVL harian**, vol/TVL, umur, konsentrasi) | Meteora+Dex | Soft |
| 6 | Volatilitas "turun-stabil" vs "mati vertikal" | Dexscreener+state | Soft + SKIP |
| 7 | Narasi: **Viralitas** + **Daya Tahan** (kualitatif+kuantitatif) | Trends/YouTube/Reddit/News | Soft |

**Hard gate gagal → SKIP (dibuang).** Yang lolos semua hard gate diberi **soft score
0–100** (bobot bisa dituning di `config.py`) → verdict STRONG/WATCH.

---

## 🧱 Struktur Kode

```
main.py                 # orkestrasi pipeline Stage 1→7
config.py               # SEMUA threshold & bobot (tuning di sini)
state.py                # anti-duplikat + riwayat harga/ATH (JSON commit-back)
scoring.py              # engine soft-score + verdict
notify.py               # format Telegram + generator link manual
sources/
  http.py               # session + rate-limit + backoff bersama
  meteora.py            # fetch pool DLMM
  dexscreener.py        # metrik token + harga SOL (cache)
  helius.py             # authority, token-2022, holder, wallet-age
  narrative.py          # pytrends + youtube + google news (degrade gracefully)
screening/
  hard_filters.py       # Stage 1-3
  holders.py            # Stage 4
  lp_quality.py         # Stage 5
  volatility.py         # Stage 6
.github/workflows/scan.yml
```

---

## ⚙️ Setup (langkah demi langkah)

### 1. Buat Bot Telegram & ambil Chat ID
1. Chat **[@BotFather](https://t.me/BotFather)** → `/newbot` → ikuti → salin **token**
   (bentuk `123456:ABC...`). Ini `TELEGRAM_BOT_TOKEN`.
2. Kirim satu pesan apa pun ke bot barumu.
3. Buka `https://api.telegram.org/bot<TOKEN>/getUpdates` di browser (ganti `<TOKEN>`).
   Cari `"chat":{"id":123456789,...}`. Angka itu `TELEGRAM_CHAT_ID`.
   - Untuk **channel/grup**: tambahkan bot sebagai admin, lalu id-nya biasanya diawali `-100`.

### 2. Ambil Helius API Key (gratis)
1. Daftar di **[dev.helius.xyz](https://dev.helius.xyz)** (free tier cukup).
2. Buat API key → salin. Ini `HELIUS_API_KEY`.

### 3. (Opsional) YouTube Data API v3 Key — untuk Stage 7 narasi
1. Buka **[Google Cloud Console](https://console.cloud.google.com/)** → buat project.
2. **APIs & Services → Enable APIs** → aktifkan **YouTube Data API v3**.
3. **Credentials → Create credentials → API key** → salin. Ini `YOUTUBE_API_KEY`.
   - Gratis 10.000 unit/hari. Kalau dikosongkan, Stage 7 tetap jalan pakai Google
     Trends + News saja (YouTube dilewati).

### 4. Set GitHub Secrets
Di repo GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
Tambahkan:

| Secret | Wajib | Isi |
|--------|-------|-----|
| `TELEGRAM_BOT_TOKEN` | ✅ | token dari BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | chat/channel id |
| `HELIUS_API_KEY` | ✅ | key Helius |
| `YOUTUBE_API_KEY` | ➖ | key YouTube (opsional) |

(Opsional) override threshold via **Variables** (bukan Secrets), mis. `NARRATIVE_ENABLED=false`.

### 5. Enable GitHub Actions
1. Push repo ini ke GitHub.
2. Tab **Actions** → aktifkan workflow bila diminta.
3. Workflow `Meteora LP Screening` jalan otomatis tiap ~5 menit (cron). Bisa juga
   dipicu manual: **Actions → Meteora LP Screening → Run workflow**.
4. Pastikan Actions punya izin tulis: **Settings → Actions → General → Workflow
   permissions → Read and write permissions** (dibutuhkan untuk commit-back state).

---

## 🖥️ Run Lokal (testing)

```bash
pip install -r requirements.txt
cp .env.example .env      # isi nilai asli
# muat env lalu jalankan (contoh dengan set inline):
DRY_RUN=true python main.py     # DRY_RUN = proses & log, TAPI tak kirim Telegram
```

`DRY_RUN=true` mencetak pesan ke log alih-alih mengirim ke Telegram — aman untuk uji.

---

## 🧠 Tuning (semua di `config.py`)

Semua ambang & bobot ada di `config.py` dan bisa dioverride lewat environment
variable (lihat `.env.example`). Bobot default (total 100) mencerminkan profil
pasif-konservatif:

| Komponen | Bobot | Kenapa |
|----------|------:|--------|
| Fee/TVL harian | 25 | Inti cuan LP pasif — yield fee riil |
| Volatilitas turun-stabil | 20 | Keberlanjutan fee di range -90% |
| Vol/TVL (velocity) | 15 | Volume aktif = fee mengalir |
| Holder health | 15 | Distribusi sehat = risiko dump rendah |
| Narasi tahan lama | 10 | Volume tahan sering ditopang narasi hidup |
| Konsentrasi LP | 10 | Risiko LP dominan tarik likuiditas |
| Umur pool | 5 | Sudah lewat fase peluncuran liar |

Keamanan kontrak **tidak** diberi bobot soft — ia **hard gate biner** (satu gagal = SKIP).

---

## 💾 State antar-run: kenapa commit-back?

Bot butuh mengingat **riwayat harga/ATH** (untuk gate ATH Stage 2) dan **pool yang
sudah dinotif** (anti-duplikat). GitHub Actions bersifat stateless, jadi ada 2 opsi:

| Opsi | Kelebihan | Kekurangan |
|------|-----------|-----------|
| **Commit file `state_data.json` balik ke repo** ✅ dipakai | Persisten, deterministik, ada audit trail di git history | Ada 1 commit "bot" tiap state berubah |
| Actions cache/artifact | Tak mengotori history | **Bisa evicted** (7 hari / kapasitas) → riwayat ATH hilang → gate ATH salah |

Karena benang merah bot ini adalah **ATH & anti-duplikat yang tak boleh hilang**,
commit-back dipilih. Commit state memakai `[skip ci]` agar tidak memicu workflow lagi,
dan workflow memakai `concurrency` guard supaya run tak tumpang tindih / merusak state.

---

## ⚠️ Keterbatasan yang jujur (yang TIDAK bisa gratis-otomatis)

Bot ini **tidak scraping** hal-hal berikut — sebagai gantinya menyediakan **link
siap-klik** di tiap notifikasi untuk verifikasi manual:

- **ATH sungguhan** → Dexscreener (API gratis) **tidak** menyediakan riwayat harga
  historis, cuma harga saat ini + persen perubahan h1/h6/h24. Sebagai gantinya bot
  memakai **GeckoTerminal** (produk CoinGecko, gratis, no API key, `sources/geckoterminal.py`)
  yang menyediakan candle OHLCV harian hingga **~6 bulan ke belakang** — kita ambil
  `high` tertinggi dari candle tsb sbg ATH sungguhan, digabung dengan riwayat state
  bot sendiri (`state.py`) sbg pelengkap/fallback kalau pool belum terindeks
  GeckoTerminal atau API-nya bermasalah (`GECKOTERMINAL_ATH_ENABLED=false` utk
  matikan). Kalau kedua sumber kosong (pool sangat baru, cold-start), bot TIDAK
  asal klaim "mencetak ATH baru" — dicek dulu tren `price_change_h24/h6`; kalau
  sedang turun konsisten, gate digagalkan. Notifikasi selalu menandai sumber ATH
  yang dipakai ("GeckoTerminal" vs "proxy sejak bot mengamati") agar transparan.
- **LP-lock / likuiditas dev terkunci** → tak bisa dipastikan 100% gratis → ditandai
  ⚠️ dan verdict STRONG diturunkan ke WATCH (lihat `DOWNGRADE_ON_WARN`). Cek manual
  via RugCheck/GMGN.
- **Phishing-tag GMGN & cluster visual Bubblemaps** → link manual disediakan.
- **Data X / Instagram / TikTok / Facebook / pump.fun community** → tak ada API
  gratis stabil (API resmi X kini berbayar) → link cashtag/community/search
  siap-klik untuk cek "vibe" manual. Narasi otomatis diproksikan lewat 4 sumber
  gratis lain: **Google Trends** (daya tahan), **YouTube Data API** (video+view+
  channel berbeda, butuh key opsional), **Reddit search.json** (post+upvote+
  subreddit berbeda, no key), **Google News RSS** (artikel+domain berbeda, no key).
  Skor dipecah 2 sumbu: **Viralitas** (breadth lintas platform + volume mentah +
  diversitas komunitas) dan **Daya Tahan** (masih hidup setelah beberapa hari vs
  cuma spike sesaat) — lihat `sources/narrative.py` untuk formulanya, semua
  threshold/cap bisa dituning di `config.py`.
- **Bin occupancy & konsentrasi LP granular per-bin** → data per-bin tak tersedia
  gratis-stabil → diestimasi dari rasio vol/TVL dan ditandai `(est)`.

Field hasil estimasi selalu ditandai `(est)` atau `⚠️` di notifikasi agar kamu tahu
mana yang perlu diverifikasi sendiri.

---

## 🔒 Keamanan

- **TIDAK ada secret hardcoded** — semua via environment / GitHub Secrets.
- `.gitignore` menutup `.env`. Jangan pernah commit token asli.
- Bot hanya membaca data publik + mengirim ke Telegram-mu. Tidak menyentuh dompet.
