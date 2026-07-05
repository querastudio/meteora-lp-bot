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
| 2 | Hard filter token (mcap, vol24h) | Dexscreener | Hard gate |
| 3 | **Keamanan kontrak** (no-mint, no-freeze, no-tax) | Helius | Hard gate |
| 4 | Distribusi holder (top10 <30%; **cluster/bundle <25%**; **coordinated trading TINGGI** = gate) | Helius | Gate |
| 5 | Kualitas LP (**fee/TVL harian**, vol/TVL, umur, konsentrasi) | Meteora+Dex | Soft |
| 6 | Volatilitas "turun-stabil" vs "mati vertikal" | Dexscreener+state | Soft + SKIP |
| 7 | Narasi: **Viralitas** + **Daya Tahan** (kualitatif+kuantitatif) + AI check organik/terkoordinasi (opsional) | Trends/YouTube/Reddit/News/Gemini+Groq | Soft |
| — | Momentum **VWAP** (harga vs rata-rata tertimbang volume sejak pool dibuat) | GeckoTerminal OHLCV | Soft |
| — | Sinyal sosial X (**Galaxy Score**/sentiment, opsional **BERBAYAR**) | LunarCrush | Soft |
| — | **Jupiter Organic Score** (volume asli vs bot/wash-trading, gratis no key) | Jupiter Tokens API v2 | Soft |

**Soal AI check narasi (Gemini + fallback Groq, opsional):** hanya menilai apakah
kutipan Reddit/News yang sudah lolos filter relevansi terlihat organik atau pola
shilling terkoordinasi — hasilnya cuma MENGALIKAN skor narasi (0.6×-1.0×), bukan
hard gate. Gemini dicoba dulu; kalau gagal/kena limit harian, otomatis fallback
ke Groq (rate limit gratisnya lebih longgar). Degrade gracefully (skor tak
berubah) kalau kedua `*_API_KEY` kosong atau API gagal. Ambil key gratis di
https://aistudio.google.com/apikey (Gemini) dan https://console.groq.com/keys (Groq).

AI check juga DI-SKIP total kalau bukti Reddit+News terlalu tipis
(`AI_MIN_REDDIT_POSTS`/`AI_MIN_NEWS_ARTICLES` di config.py) — lebih baik
"tak menilai" drpd LLM memaksa vonis organik/terkoordinasi dari 1-2 kutipan
yang bahkan bisa salah topik (Google News RSS kadang match longgar).

**Soal LunarCrush (opsional):** dipakai karena X/Twitter adalah sumber narasi
utama dunia memecoin dan tak ada jalan resmi+gratis+ToS-aman lain utk
mengaksesnya (X API resmi $200+/bln; scraping = risiko ban & pelanggaran ToS,
lihat riwayat diskusi). **Coba dulu pakai API key dari tier GRATIS** LunarCrush
(sign up di https://lunarcrush.com/pricing/, tanpa kartu kredit) — status
apakah tier gratis benar-benar include data sosial masih belum pasti dari
dokumentasi publik mereka, jadi kita verifikasi langsung dari log (401/403
di log = perlu upgrade ~$24/bln tier Individual; kalau berhasil, gratis).
**Keterbatasan lain yang sudah dikonfirmasi manual:** LunarCrush TIDAK
meng-index token yang baru rilis hitungan jam — jadi utk kandidat paling
fresh, sinyal ini nyaris selalu n/a (wajar, bukan bug). Kosongkan
`LUNARCRUSH_API_KEY` utk skip total (tak ada dampak ke pipeline).

**Soal Jupiter Organic Score (gratis, no key):** BUKAN sinyal narasi — ini
sinyal legitimasi volume (organic volume/holders/traders/buyers vs bot/
wash-trading) dari Jupiter, agregator DEX terbesar Solana yang melihat
semua venue trading. Penguat utk deteksi coordinated-trading/wash-trading
di samping heuristik holder/volatilitas yang sudah ada — soft score, bukan
hard gate. API masih "V2 (Beta)" per dokumentasi Jupiter, jadi skema bisa
berubah; degrade gracefully kalau parsing gagal.

**Hard gate gagal → SKIP (dibuang).** Yang lolos semua hard gate diberi **soft score
0–100** (bobot bisa dituning di `config.py`) → verdict STRONG/WATCH.

---

## 🧱 Struktur Kode

```
main.py                 # orkestrasi pipeline Stage 1→7
config.py               # SEMUA threshold & bobot (tuning di sini)
state.py                # anti-duplikat + riwayat harga utk Stage 6 (JSON commit-back)
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
3. Workflow `Meteora LP Screening` PUNYA jadwal `schedule: */5 * * * *`. Bisa juga
   dipicu manual: **Actions → Meteora LP Screening → Run workflow**.
4. Pastikan Actions punya izin tulis: **Settings → Actions → General → Workflow
   permissions → Read and write permissions** (dibutuhkan untuk commit-back state).

> ⚠️ **PENTING — keterbatasan `schedule` GitHub Actions:** GitHub **secara resmi
> mendokumentasikan** bahwa jadwal cron yang sangat sering (tiap 5 menit) bisa
> **ditunda/didegradasi**, terutama untuk repo yang tak terlalu aktif — dalam
> praktik sering jadi cuma **~1x per jam**, bukan tiap 5 menit. Ini bukan bug di
> kode bot, ini keterbatasan platform GitHub. Karena memecoin bisa "hidup-mati"
> dalam hitungan menit-jam, keterlambatan ini bisa bikin banyak token terlewat.
>
> **Solusi (opsional tapi disarankan): trigger dari luar via `workflow_dispatch`**
> (yang TIDAK kena degradasi jadwal, selalu jalan cepat) memakai layanan cron
> gratis pihak ketiga. Lihat bagian "⏱️ Trigger 5 menit yang andal" di bawah.

---

## ⏱️ Trigger 5 menit yang andal (mengatasi keterbatasan `schedule` GitHub)

Karena `schedule:` bawaan GitHub Actions tak bisa diandalkan utk interval 5 menit
(lihat catatan di atas), pakai **cron eksternal gratis** ([cron-job.org](https://cron-job.org),
mendukung interval hingga 1 menit) untuk memanggil `workflow_dispatch` API GitHub
langsung — jalur ini TIDAK kena degradasi jadwal.

### Langkah 1 — Buat GitHub Token (scope terbatas, cuma repo ini)
1. GitHub → **Settings** (akun, bukan repo) → **Developer settings** →
   **Personal access tokens → Fine-grained tokens** → **Generate new token**.
2. **Resource owner**: akunmu. **Repository access**: **Only select repositories**
   → pilih `meteora-lp-bot` saja (JANGAN "All repositories" — batasi blast radius).
3. **Permissions → Repository permissions → Actions**: pilih **Read and write**.
4. Set masa berlaku (mis. 1 tahun), **Generate token**, salin token-nya
   (`github_pat_...`) — ini cuma tampil sekali.

### Langkah 2 — Daftar cron-job.org (gratis) & atur job
1. Daftar di **[cron-job.org](https://console.cron-job.org)**.
2. **Create cronjob**:
   - **URL**: `https://api.github.com/repos/querastudio/meteora-lp-bot/actions/workflows/scan.yml/dispatches`
   - **Request method**: `POST`
   - **Headers** (tambah 3 baris):
     - `Accept: application/vnd.github+json`
     - `Authorization: Bearer <TOKEN_DARI_LANGKAH_1>`
     - `Content-Type: application/json`
   - **Body**: `{"ref":"main"}`
   - **Schedule**: every 5 minutes (`*/5 * * * *`)
3. Simpan. Cron-job.org akan memanggil GitHub setiap 5 menit persis, memicu
   workflow lewat `workflow_dispatch` (jalur cepat, bukan `schedule`).

⚠️ **Catatan keamanan**: token GitHub ini tersimpan di server cron-job.org
(pihak ketiga), bukan di GitHub Secrets. Scope-nya sudah dibatasi HANYA ke repo
ini + izin Actions saja (bukan akses penuh akun), jadi risiko kalau bocor
minimal. Kalau ingin berhenti, hapus token di GitHub kapan saja.

Jadwal `schedule` bawaan tetap dibiarkan aktif di `scan.yml` sebagai **cadangan**
(kalau cron-job.org down, setidaknya masih ada run ~1x/jam dari GitHub sendiri).

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

Bot butuh mengingat **riwayat harga** (untuk estimasi volume-tahan-lama Stage 6)
dan **pool yang sudah dinotif** (anti-duplikat). GitHub Actions bersifat stateless,
jadi ada 2 opsi:

| Opsi | Kelebihan | Kekurangan |
|------|-----------|-----------|
| **Commit file `state_data.json` balik ke repo** ✅ dipakai | Persisten, deterministik, ada audit trail di git history | Ada 1 commit "bot" tiap state berubah |
| Actions cache/artifact | Tak mengotori history | **Bisa evicted** (7 hari / kapasitas) → riwayat harga hilang |

Karena riwayat harga & anti-duplikat tak boleh hilang, commit-back dipilih. Commit
state memakai `[skip ci]` agar tidak memicu workflow lagi, dan workflow memakai
`concurrency` guard supaya run tak tumpang tindih / merusak state.

---

## ⚠️ Keterbatasan yang jujur (yang TIDAK bisa gratis-otomatis)

Bot ini **tidak scraping** hal-hal berikut — sebagai gantinya menyediakan **link
siap-klik** di tiap notifikasi untuk verifikasi manual:

- **LP-lock / likuiditas dev terkunci** → tak bisa dipastikan 100% gratis → ditandai
  ⚠️ dan verdict STRONG diturunkan ke WATCH (lihat `DOWNGRADE_ON_WARN`). Cek manual
  via RugCheck/GMGN.
- **Cluster/bundle detection (ala GMGN/DevsNightmare/GodMode)** → tool-tool ini
  proprietary, TIDAK ADA API gratis publik untuk memanggilnya langsung. Bot
  membangun deteksi cluster SENDIRI pakai Helius (gratis, sudah dipakai):
  wallet top holder yang "lahir" (tx pertama terlihat) dalam jendela waktu
  sempit (`CLUSTER_TIME_WINDOW_SECONDS`, default 10 menit) dikelompokkan
  sbg 1 kemungkinan entitas. Ini **proxy waktu**, BUKAN exact funding-source
  match seperti GMGN (yang trace persis siapa danai wallet mana) — jadi bisa
  ada false-negative (cluster asli tak kedeteksi kalau wallet-nya "dipanaskan"/
  dibuat jauh-jauh hari sebelum dipakai). Hard gate: cluster terbesar <25%
  supply (`MAX_CLUSTER_SUPPLY_PCT`, dituning di config.py) — sesuai prinsip
  "bundler boleh ada, asal tak kuasai mayoritas". Untuk verifikasi visual lebih
  dalam (funding chain sungguhan, phishing-tag), link GMGN/Bubblemaps/
  DevsNightmare/Deepnets tetap disediakan di notifikasi.
- **Coordinated trading / wash trading (top20)** → tanpa panggilan API
  tambahan, bot hitung persentase top20 holder yang **fresh** (tx sedikit),
  **saldo rendah** (`< EMPTY_WALLET_SOL_USD`), dan **umur muda**
  (`< WALLET_YOUNG_AGE_HOURS`, default 24 jam). Kalau rata-rata ketiganya
  seragam TINGGI (`COORDINATION_HIGH_PCT`, default 70%) → hard gate SKIP
  (indikasi kuat bundling/wash trading). Ini sample **top20**, bukan top100
  penuh — cek top100 individual butuh ~5x panggilan Helius lebih banyak per
  token, berisiko rate-limit & bikin cron 5 menit terlalu lambat; top20 sudah
  representatif untuk pola paling umum (bundler biasanya pakai wallet baru
  dalam jumlah besar yang tercermin di top holder mana pun diambil sampelnya).
- **Data X / Instagram / TikTok / Facebook / pump.fun community** → tak ada API
  gratis stabil (API resmi X kini berbayar) → link cashtag/community/search
  siap-klik untuk cek "vibe" manual. Narasi otomatis diproksikan lewat 4 sumber
  gratis lain: **Google Trends** (daya tahan), **YouTube Data API** (video+view+
  channel berbeda, butuh key opsional), **Reddit search.json** (post+upvote+
  subreddit berbeda, no key), **Google News RSS** (artikel+domain berbeda, no key).
  Skor dipecah 2 sumbu: **Viralitas** (breadth lintas platform + volume mentah +
  diversitas komunitas) dan **Daya Tahan** (masih hidup setelah beberapa hari vs
  cuma spike sesaat) — lihat `sources/narrative.py` untuk formulanya, semua
  threshold/cap bisa dituning di `config.py`. Notifikasi juga menampilkan
  **"Konteks"**: kutipan ASLI (judul post Reddit ter-upvote / artikel berita)
  supaya user tahu SIAPA/APA yang dibahas (mis. "ANSEM confirms airdrop wave 2"),
  bukan cuma angka. Ini kutipan nyata dari sumber, BUKAN ringkasan otomatis
  buatan bot — kalau tak ada post/artikel yang relevan, kolom ini kosong (bot
  tak mengarang cerita).
- **Bin occupancy & konsentrasi LP granular per-bin** → data per-bin tak tersedia
  gratis-stabil → diestimasi dari rasio vol/TVL dan ditandai `(est)`.

Field hasil estimasi selalu ditandai `(est)` atau `⚠️` di notifikasi agar kamu tahu
mana yang perlu diverifikasi sendiri.

---

## 🔒 Keamanan

- **TIDAK ada secret hardcoded** — semua via environment / GitHub Secrets.
- `.gitignore` menutup `.env`. Jangan pernah commit token asli.
- Bot hanya membaca data publik + mengirim ke Telegram-mu. Tidak menyentuh dompet.
