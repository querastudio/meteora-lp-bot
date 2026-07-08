"""
sources/gmgn.py — GMGN OpenAPI: keamanan token, dev holding %, dan count wallet
smart money/sniper/rat_trader/renowned/whale -- PELENGKAP due diligence
Stage 3/4 kita sendiri (Helius + heuristik cluster-waktu), BUKAN pengganti.
GMGN nge-tag wallet dari funding-source tracing ASLI (mereka trace siapa
danai wallet), bukan proxy waktu-pembuatan spt punya kita -- lihat catatan
keterbatasan di screening/holders.py.

API GRATIS -- diverifikasi langsung: apply cukup submit public key Ed25519
sekali via https://gmgn.ai/ai, langsung dapat API key tanpa prompt bayar
(restriksi "Trading Disabled" otomatis di-set, pas krn bot ini read-only).

Auth utk endpoint READ-ONLY yang kita pakai ("Exist Auth") cukup header
X-APIKEY + query param timestamp (unix seconds) + client_id (UUID acak per
request) -- TIDAK PERNAH butuh signing Ed25519 (itu cuma wajib utk endpoint
trading/wallet-follow yang bot ini tak pernah panggil). Lihat
src/client/OpenApiClient.ts & signer.ts di github.com/GMGNAI/gmgn-skills
utk sumber detail auth ini.

Skema field respons BELUM 100% terdokumentasi resmi (GMGN tak kasih contoh
JSON mentah di docs publik), dan docs resmi mereka TERBUKTI MELESET dari API
sungguhan (docs/workflow-token-due-diligence.md sebut rug_ratio/sniper_count/
owner_renounced yg TAK ADA di respons nyata) -- semua field di bawah sudah
dikoreksi berdasarkan log respons LIVE, bukan lagi dugaan dari docs:
  - token/security: is_honeypot/is_open_source (tri-state, sering None =
    belum dianalisis GMGN, BUKAN "aman"), renounced_mint/
    renounced_freeze_account, buy_tax/sell_tax/top_10_holder_rate, plus
    lock_summary (LP-lock/burn, tak disebut docs sama sekali) -- lihat
    docstring token_security().
  - token/info: dev holding % dihitung dari dev.creator_token_balance /
    circulating_supply (field 'dev' berisi objek creator, bukan % langsung
    -- lihat docstring dev_holding()); count wallet smart/sniper/rat/
    renowned/whale dari wallet_tags_stat (lihat docstring top_holder_tags()
    utk kenapa market/token_top_holders per-baris TAK dipakai lagi).

INFORMASIONAL SAJA: hasil di sini tampil sbg konteks tambahan di notifikasi
(HARD GATES section), TIDAK PERNAH jadi hard gate baru atau menyentuh skor
-- hard gate keamanan/holder tetap sepenuhnya dari Helius (main.py/
hard_filters.py/holders.py), sesuai kesepakatan sebelum implementasi ini.
Gagal/API mati -> degrade gracefully, JANGAN crash run.
"""

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import config
from sources import http

log = logging.getLogger("gmgn")

BASE = "https://openapi.gmgn.ai"


def _auth_params(extra: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(extra)
    params["timestamp"] = int(time.time())
    params["client_id"] = str(uuid.uuid4())
    return params


def _headers() -> Dict[str, str]:
    return {"X-APIKEY": config.GMGN_API_KEY, "Content-Type": "application/json"}


def _get(subpath: str, params: Dict[str, Any]) -> Optional[Any]:
    if not config.GMGN_ENABLED or not config.GMGN_API_KEY:
        return None
    resp = http.get_json(
        f"{BASE}{subpath}",
        params=_auth_params(params),
        headers=_headers(),
        timeout=config.HTTP_TIMEOUT,
    )
    if not resp:
        return None
    # Skema wrapper umum ala REST Asia (code/msg/data) -- unwrap kalau ada.
    if isinstance(resp, dict) and "data" in resp:
        return resp["data"]
    return resp


def _tri_bool(v: Any) -> Optional[bool]:
    """None kalau field memang null (GMGN blm sempat analisis), else bool asli."""
    return None if v is None else bool(v)


def token_security(mint: str, chain: str = "sol") -> Dict[str, Any]:
    """
    Return { available, is_honeypot, open_source, renounced_mint,
             renounced_freeze, buy_tax, sell_tax, top10_holder_rate,
             lp_locked, lp_lock_pct, flags }.

    Field DIKONFIRMASI dari respons LIVE /v1/token/security (BUKAN dari
    docs resmi GMGN yg ternyata skemanya beda dari API sungguhan --
    docs/workflow-token-due-diligence.md sebut field rug_ratio/
    sniper_count/owner_renounced yg TAK ADA sama sekali di respons nyata;
    sebaliknya ada lock_summary (info lock/burn LP) yg tak disebut docs
    sama sekali -- verified 6 Juli 2026 via live run).

    is_honeypot/open_source SERING None (bukan True/False) -- GMGN belum
    selalu menganalisis token baru utk pengecekan ini. None ditampilkan
    apa adanya sbg "n/a" di notif, TIDAK diasumsikan aman (beda dari
    asumsi awal yg salah menganggap None -> False/"aman").

    lp_locked/lp_lock_pct dari lock_summary -- BONUS tak terduga: bisa isi
    celah "LP-lock belum terverifikasi otomatis" yg selama ini jadi
    warning tetap di tiap notifikasi (lihat hard_filters.py). Belum
    dipakai gantikan warning itu -- cuma ditampilkan sbg info tambahan
    dulu, nunggu diskusi lanjut sebelum ubah logic warning yg sudah ada.
    """
    out = {
        "available": False, "is_honeypot": None, "open_source": None,
        "renounced_mint": None, "renounced_freeze": None,
        "buy_tax": 0.0, "sell_tax": 0.0, "top10_holder_rate": 0.0,
        "lp_locked": None, "lp_lock_pct": 0.0, "flags": [],
    }
    try:
        data = _get("/v1/token/security", {"chain": chain, "address": mint})
        if not data:
            return out
        d = data[0] if isinstance(data, list) and data else data
        if not isinstance(d, dict):
            return out

        is_honeypot = _tri_bool(d.get("is_honeypot"))
        open_source = _tri_bool(d.get("is_open_source"))
        renounced_mint = _tri_bool(d.get("renounced_mint"))
        renounced_freeze = _tri_bool(d.get("renounced_freeze_account"))
        buy_tax = float(d.get("buy_tax", 0) or 0)
        sell_tax = float(d.get("sell_tax", 0) or 0)
        top10 = float(d.get("top_10_holder_rate", 0) or 0)

        lock = d.get("lock_summary") or {}
        lp_locked = _tri_bool(lock.get("is_locked"))
        lock_detail = lock.get("lock_detail") or []
        lp_lock_pct = (
            sum(float(x.get("percent", 0) or 0) for x in lock_detail if isinstance(x, dict))
            if isinstance(lock_detail, list) else 0.0
        )

        flags: List[str] = []
        if is_honeypot:
            flags.append("GMGN: terdeteksi HONEYPOT")
        if open_source is False:
            flags.append("GMGN: source code belum terverifikasi")
        if buy_tax > 0.10 or sell_tax > 0.10:
            flags.append(f"GMGN: tax tinggi (buy {buy_tax*100:.0f}% / sell {sell_tax*100:.0f}%)")

        out.update({
            "available": True, "is_honeypot": is_honeypot, "open_source": open_source,
            "renounced_mint": renounced_mint, "renounced_freeze": renounced_freeze,
            "buy_tax": buy_tax, "sell_tax": sell_tax, "top10_holder_rate": round(top10, 3),
            "lp_locked": lp_locked, "lp_lock_pct": round(lp_lock_pct * 100.0, 1),
            "flags": flags,
        })
        log.info(
            "GMGN token/security OK utk mint %s...: honeypot=%s open_source=%s "
            "lp_locked=%s (%.1f%%) top10=%.1f%%",
            mint[:6], is_honeypot, open_source, lp_locked, out["lp_lock_pct"], top10 * 100,
        )
    except Exception as e:  # noqa: BLE001
        log.info("GMGN token/security gagal utk mint %s...: %s (degrade)", mint[:6], e)
    return out


def dev_holding(mint: str, chain: str = "sol") -> Dict[str, Any]:
    """
    Return { available, dev_holding_pct, dev_status }.

    DIKONFIRMASI dari live log (6 Juli 2026): token/info TAK punya field %
    langsung -- field 'dev' berisi OBJEK info creator (creator_address,
    creator_token_balance, creator_token_status spt "creator_close"), bukan
    salah satu dari kandidat lama (dev_holding_rate/dev_holding_percentage/
    dst -- semua itu TAK ADA di respons nyata). dev_holding_pct dihitung
    manual: creator_token_balance / circulating_supply.

    Di 3 sampel live (ZERO/Jotchua/PUMPCADE) creator_token_status selalu
    "creator_close" & balance "0" -- dev sudah keluar total, ini SINYAL ASLI
    (bukan bug/field kosong), makanya dev_status ikut ditampilkan biar jelas
    "0%" itu artinya "sudah cair", bukan "data kosong".
    """
    out = {"available": False, "dev_holding_pct": 0.0, "dev_status": ""}
    try:
        data = _get("/v1/token/info", {"chain": chain, "address": mint})
        if not data:
            return out
        d = data[0] if isinstance(data, list) and data else data
        if not isinstance(d, dict):
            return out

        dev = d.get("dev")
        circ = float(d.get("circulating_supply", 0) or 0)
        if isinstance(dev, dict) and circ > 0:
            bal = float(dev.get("creator_token_balance", 0) or 0)
            out["dev_holding_pct"] = round(bal / circ * 100.0, 2)
            out["dev_status"] = str(dev.get("creator_token_status") or "")
            out["available"] = True

        if out["available"]:
            log.info(
                "GMGN dev_holding OK utk mint %s...: %.2f%% status=%s",
                mint[:6], out["dev_holding_pct"], out["dev_status"],
            )
        else:
            log.info("GMGN dev_holding: field 'dev'/circulating_supply tak lengkap utk mint %s...", mint[:6])
    except Exception as e:  # noqa: BLE001
        log.info("GMGN dev_holding gagal utk mint %s...: %s (degrade)", mint[:6], e)
    return out


def volume_momentum(mint: str, chain: str = "sol") -> Dict[str, Any]:
    """
    Return { available, volume_1m, volume_5m, volume_1h, buy_volume_5m,
             sell_volume_5m, swaps_5m }.

    Per keluhan user: notif kadang telat terasa krn cron 5 menit (bisa lag
    lagi kalau GitHub Actions sibuk) + hard gate top10 kadang menahan token
    pas lagi paling terkonsentrasi/berisiko (baru lolos begitu konsentrasi
    turun -- itu gate bekerja sesuai desain, BUKAN bug -- lihat diskusi
    sesi ini). Metrik ini TAK mengubah timing/gate itu, tapi kasih user
    visibilitas MOMENTUM TERKINI (5 menit terakhir) langsung di notif,
    biar user sendiri bisa nilai apakah notif ini masih "segar" (vol 5m
    msh tinggi) atau sudah lewat puncak (vol 5m sudah turun jauh) sebelum
    ambil keputusan LP.

    Field 'price' pada /v1/token/info SUDAH kekonfirmasi live (sesi ini,
    verifikasi dev_holding/wallet_tags_stat) py punya volume_1m/volume_5m/
    volume_1h/buy_volume_5m/sell_volume_5m/swaps_5m -- SAMA persis
    endpoint yg sudah dipanggil dev_holding(), cuma field ini blm pernah
    diekstrak. Panggilan HTTP terpisah (bukan digabung ke dev_holding())
    demi jaga kontrak return dev_holding() yg sudah dipakai main.py --
    sedikit redundan (2x call /v1/token/info per kandidat) tp GMGN gratis
    & sudah di-throttle (lihat sources/http.py).

    INFORMASIONAL SAJA spt integrasi GMGN lain di sesi ini -- TAK
    menyentuh skor/hard gate (beda dari vwap_momentum yg memang sudah
    berbobot skor via GeckoTerminal).
    """
    out = {
        "available": False, "volume_1m": 0.0, "volume_5m": 0.0, "volume_1h": 0.0,
        "buy_volume_5m": 0.0, "sell_volume_5m": 0.0, "swaps_5m": 0,
    }
    try:
        data = _get("/v1/token/info", {"chain": chain, "address": mint})
        if not data:
            return out
        d = data[0] if isinstance(data, list) and data else data
        if not isinstance(d, dict):
            return out

        price = d.get("price")
        if not isinstance(price, dict):
            return out

        out.update({
            "available": True,
            "volume_1m": float(price.get("volume_1m", 0) or 0),
            "volume_5m": float(price.get("volume_5m", 0) or 0),
            "volume_1h": float(price.get("volume_1h", 0) or 0),
            "buy_volume_5m": float(price.get("buy_volume_5m", 0) or 0),
            "sell_volume_5m": float(price.get("sell_volume_5m", 0) or 0),
            "swaps_5m": int(price.get("swaps_5m", 0) or 0),
        })
        log.info(
            "GMGN volume_momentum OK utk mint %s...: 1m=$%.0f 5m=$%.0f 1h=$%.0f (buy5m=$%.0f/sell5m=$%.0f, %d swap)",
            mint[:6], out["volume_1m"], out["volume_5m"], out["volume_1h"],
            out["buy_volume_5m"], out["sell_volume_5m"], out["swaps_5m"],
        )
    except Exception as e:  # noqa: BLE001
        log.info("GMGN volume_momentum gagal utk mint %s...: %s (degrade)", mint[:6], e)
    return out


def ath_price(mint: str, chain: str = "sol") -> Dict[str, Any]:
    """
    Return { available, ath_price_usd, candle_count }.

    Bug nyata (dilaporkan user via screenshot chart GMGN $NEIL, 8 Juli
    2026): badge "ATH BARU" kita muncul PADAHAL harga jelas jatuh drastis
    dari puncak asli token (garis "All Time High" GMGN jauh di atas harga
    skrg). Akar masalah: tracking ATH lokal kita (state.py, dari
    price_history sendiri) DIBATASI ~1.4 hari (400 titik @5menit) DAN
    sempat py field "ath" basi peninggalan kode lama yg sudah mati (lihat
    riwayat sesi ini) -- utk token yg puncak aslinya terjadi SBLM kita mulai
    pantau (atau sblm ATH-tracking diperbaiki), baseline kita jadi jauh di
    bawah puncak sungguhan, bikin bounce kecil di tengah downtrend salah
    kebaca "ATH baru".

    Fix: pakai candle HARIAN (resolution=1d) dari /v1/market/token_kline
    (endpoint resmi GMGN, dikonfirmasi via docs github.com/GMGNAI/gmgn-skills
    -- field high/low/open/close/volume/amount per candle, BELUM pernah
    kita panggil sblm ini di proyek ini, jadi field blm terverifikasi
    live 100% spt endpoint lain -- log skema mentah candle pertama sekali).
    MAX(high) dari SELURUH candle = ATH sungguhan token ini, independen dari
    seberapa lama/jauh kita sendiri sudah mulai tracking-nya -- 1 call
    ringan, resolution harian cukup cover umur token memecoin tipikal
    (hari-bulan) tanpa respons kegedean.
    """
    out = {"available": False, "ath_price_usd": 0.0, "candle_count": 0}
    try:
        # GMGN nolak "from": 0 (dicoba live 8 Juli 2026 -> 400 BAD_REQUEST
        # "from get 0 but must be a valid timestamp in ms") -- dua kesalahan
        # sekaligus: (1) API minta MILIDETIK bukan detik, (2) literal 0 tak
        # dianggap timestamp valid sama sekali. Pakai 400 hari ke belakang
        # (cukup cover umur token memecoin tipikal) dlm ms, bukan epoch 0.
        from_ms = int((time.time() - 400 * 86400) * 1000)
        to_ms = int(time.time() * 1000)
        data = _get(
            "/v1/market/token_kline",
            {"chain": chain, "address": mint, "resolution": "1d", "from": from_ms, "to": to_ms},
        )
        if not data:
            log.info("GMGN ath_price: respons kosong/gagal utk mint %s... (degrade)", mint[:6])
            return out
        rows = data if isinstance(data, list) else (data.get("list") or [])
        if not rows:
            log.info(
                "GMGN ath_price: tak ada candle utk mint %s... (respons: %s)",
                mint[:6], str(data)[:200],
            )
            return out

        if isinstance(rows[0], dict):
            log.info(
                "GMGN ath_price RAW candle pertama (semua field) utk mint %s...: %s",
                mint[:6], rows[0],
            )

        highs: List[float] = []
        for c in rows:
            if not isinstance(c, dict):
                continue
            try:
                highs.append(float(c.get("high", 0) or 0))
            except (TypeError, ValueError):
                continue
        if not highs:
            return out

        out.update({
            "available": True, "ath_price_usd": max(highs), "candle_count": len(rows),
        })
        log.info(
            "GMGN ath_price OK utk mint %s...: ATH $%.10f dari %d candle harian",
            mint[:6], out["ath_price_usd"], len(rows),
        )
    except Exception as e:  # noqa: BLE001
        log.info("GMGN ath_price gagal utk mint %s...: %s (degrade)", mint[:6], e)
    return out


def top_holder_tags(mint: str, chain: str = "sol") -> Dict[str, Any]:
    """
    Return { available, smart_money_count, sniper_count, rat_trader_count,
             renowned_count, whale_count, holder_count }.

    DIKOREKSI TOTAL dari desain awal. Desain awal menyisir baris per-holder
    dari /v1/market/token_top_holders (field tags/maker_token_tags/
    wallet_tag_v2) mencari kategori smart_degen/bundler/sniper/rat_trader --
    TERNYATA field itu TAK PERNAH berisi kategori itu di 3 sampel live:
    wallet_tag_v2 cuma label ranking ("TOP1"/"TOP2"), maker_token_tags cuma
    peran on-chain ("top_holder"/"transfer_in"), tags cuma nama platform
    trading ("bullx"/"padre") atau "fresh_wallet". Kategori yg kita cari
    justru ada di 'wallet_tags_stat' pada /v1/token/info (endpoint SAMA
    dgn dev_holding()) -- tapi berupa COUNT wallet per kategori, bukan %
    supply (GMGN tak expose breakdown % per kategori scr langsung).

    'bundler_wallets' & 'fresh_wallets' SENGAJA TAK dipakai/ditampilkan --
    di ke-3 sampel live (3 token beda, tanpa hubungan) keduanya PERSIS 1000,
    indikasi kuat itu cuma nilai cap/placeholder API, bukan hitungan asli.
    smart/renowned/sniper/rat/whale bervariasi wajar antar token jadi
    dianggap sinyal asli.
    """
    out = {
        "available": False, "smart_money_count": 0, "sniper_count": 0,
        "rat_trader_count": 0, "renowned_count": 0, "whale_count": 0,
        "holder_count": 0,
    }
    try:
        data = _get("/v1/token/info", {"chain": chain, "address": mint})
        if not data:
            return out
        d = data[0] if isinstance(data, list) and data else data
        if not isinstance(d, dict):
            return out
        stat = d.get("wallet_tags_stat")
        if not isinstance(stat, dict):
            return out

        out.update({
            "available": True,
            "smart_money_count": int(stat.get("smart_wallets", 0) or 0),
            "sniper_count": int(stat.get("sniper_wallets", 0) or 0),
            "rat_trader_count": int(stat.get("rat_trader_wallets", 0) or 0),
            "renowned_count": int(stat.get("renowned_wallets", 0) or 0),
            "whale_count": int(stat.get("whale_wallets", 0) or 0),
            "holder_count": int(d.get("holder_count", 0) or 0),
        })
        log.info(
            "GMGN wallet_tags_stat OK utk mint %s...: smart=%d renowned=%d sniper=%d rat=%d whale=%d (holder_count=%d)",
            mint[:6], out["smart_money_count"], out["renowned_count"], out["sniper_count"],
            out["rat_trader_count"], out["whale_count"], out["holder_count"],
        )
    except Exception as e:  # noqa: BLE001
        log.info("GMGN wallet_tags_stat gagal utk mint %s...: %s (degrade)", mint[:6], e)
    return out


_SCAM_PATTERN_TAGS = {
    "wash_trader": "wash_trader_pct",
    "sandwich_bot": "sandwich_bot_pct",
    "bundler": "bundler_pct",
    "rat_trader": "rat_trader_pct",
    "fresh_wallet": "fresh_pct",
}

# Sample minimum spy coefficient-of-variation bermakna (di bawah ini, noise
# dari sample kecil terlalu besar utk disimpulkan apa-apa).
_BUNDLER_MIN_SAMPLE = 5


def _coeff_variation(values: List[float]) -> Optional[float]:
    """Coefficient of variation (stddev/|mean|) -- None kalau sample/mean tak layak."""
    n = len(values)
    if n < _BUNDLER_MIN_SAMPLE:
        return None
    mean = sum(values) / n
    if mean == 0:
        return None
    variance = sum((v - mean) ** 2 for v in values) / n
    return (variance ** 0.5) / abs(mean)


def _uniformity_signal(cv: Optional[float]) -> Optional[float]:
    """
    cv=0 (semua wallet PERSIS sama) -> 1.0 (indikasi bundler kuat).
    cv>=0.5 (variasi wajar antar wallet independen) -> 0.0 (organik).
    Ambang 0.5 dipilih longgar SENGAJA -- distribusi holder organik nyata
    (saldo/umur/harga beli macam-macam) biasanya CV jauh > 0.5; wallet yg
    dibuat/didanai/dibeli bareng-bareng (bundler) biasanya CV << 0.5.
    """
    if cv is None:
        return None
    return max(0.0, min(1.0, 1.0 - cv / 0.5))


def _bundler_cluster_signal(
    sol_bal_usd: List[float],
    wallet_age_days: List[float],
    buy_mc_usd: List[float],
    remaining_pct: List[float],
    holding_days: List[float],
    funding_counter: Dict[str, int],
    funding_n: int,
) -> Dict[str, Any]:
    """
    Gabungkan 6 heuristik user (SOL balance/wallet age/bought avg mc/
    remaining supply/funding source/holding duration) jadi 1 skor 0-100 +
    label. Prinsip: independen/organik = VARIASI TINGGI antar wallet;
    bundler/koordinasi = wallet-wallet itu "kembar" -- saldo, umur, harga
    beli, % supply, lama hold SAMA/mirip, dan/atau banyak yg didanai dari
    SATU alamat yg sama (funding source) -- funding_counter dari
    native_transfer.from_address, tracing ASLI GMGN, bukan proxy.

    5 metrik numerik pakai coefficient-of-variation (lihat _uniformity_signal);
    funding source pakai % wallet yg berbagi SATU funding address terbanyak
    (butuh >=2 wallet berbagi drpd noise 1 wallet kebetulan).

    INFORMASIONAL SAJA (skor 0-100 ditampilkan, tak menyentuh hard gate/skor
    LP) -- konsisten dgn semua sinyal GMGN top100 lain di modul ini; ini
    murni statistik pendekatan (bukan funding-source tracing PER-PASANGAN
    ala GMGN skill resmi yg trace SETIAP wallet lawan SETIAP wallet lain),
    jadi TETAP disarankan cross-check manual via link Bubblemaps/GMGN utk
    kasus yg skornya tinggi.
    """
    out = {
        "available": False, "score": 0.0, "label": "n/a",
        "sample_count": 0,
        "signals": {
            "sol_balance": None, "wallet_age": None, "bought_avg_mc": None,
            "remaining_supply": None, "holding_duration": None, "funding_source": None,
        },
        "top_funding_share_pct": 0.0, "top_funding_wallet_count": 0,
    }
    signals: Dict[str, Optional[float]] = {
        "sol_balance": _uniformity_signal(_coeff_variation(sol_bal_usd)),
        "wallet_age": _uniformity_signal(_coeff_variation(wallet_age_days)),
        "bought_avg_mc": _uniformity_signal(_coeff_variation(buy_mc_usd)),
        "remaining_supply": _uniformity_signal(_coeff_variation(remaining_pct)),
        "holding_duration": _uniformity_signal(_coeff_variation(holding_days)),
        "funding_source": None,
    }
    if funding_n >= 2 and funding_counter:
        top_addr, top_count = max(funding_counter.items(), key=lambda kv: kv[1])
        if top_count >= 2:
            share = top_count / funding_n
            signals["funding_source"] = share
            out["top_funding_share_pct"] = round(share * 100.0, 1)
            out["top_funding_wallet_count"] = top_count

    out["signals"] = {k: (round(v, 2) if v is not None else None) for k, v in signals.items()}
    available_signals = [v for v in signals.values() if v is not None]
    # Butuh minimal 2 dari 6 sinyal punya cukup data -- sesuai prinsip
    # "channels_blind" yg sudah dipakai di narrative.py: satu sinyal noise
    # kecil tak boleh menyimpulkan apa-apa sendirian.
    if len(available_signals) < 2:
        return out

    score = sum(available_signals) / len(available_signals) * 100.0
    out["available"] = True
    out["score"] = round(score, 1)
    out["sample_count"] = len(available_signals)
    if score >= 70:
        out["label"] = "🔴 INDIKASI KUAT bundler/koordinasi"
    elif score >= 40:
        out["label"] = "🟡 indikasi SEDANG bundler/koordinasi"
    else:
        out["label"] = "✅ variasi wajar (organik)"
    return out


def top100_cluster_analysis(
    mint: str,
    market_cap_usd: float = 0.0,
    price_usd: float = 0.0,
    sol_price: float = 0.0,
    chain: str = "sol",
) -> Dict[str, Any]:
    """
    Return { available, sample_count, coverage_pct, scam_risk_pct,
             fresh_pct, wash_trader_pct, sandwich_bot_pct, bundler_pct,
             rat_trader_pct, is_new_pct, is_suspicious_pct, bundler_cluster }
             (tiap _pct punya pasangan _count).

    Fitur "Top 100 Holders Analysis" SUNGGUHAN spt skill resmi GMGN (beda
    dari top_holder_tags() yg cuma count level-token dari wallet_tags_stat)
    -- deteksi POLA SCAM (cluster bundler, wash trading, sandwich bot,
    fresh-wallet farm) dgn agregasi % SUPPLY (bukan cuma jumlah wallet)
    per kategori, dari /v1/market/token_top_holders (top 100 baris asli,
    per-wallet, funding-source tracing GMGN -- bukan proxy).

    Vocabulary tag DIKONFIRMASI dari log live (6 Juli 2026, 3 token nyata):
    field 'tags' per-wallet berisi "wash_trader"/"sandwich_bot"/
    "fresh_wallet"/"bluechip_owner"/"kol"/"fomo" + tag platform trading
    (axiom/photon/trojan/bullx/padre/gmgn -- bukan sinyal risiko, cuma
    tool yg dipakai wallet itu trading, DIABAIKAN di sini). "bundler" &
    "rat_trader" belum pernah muncul di 3 sampel, tapi tetap dimasukkan
    (konsisten dgn kategori wallet_tags_stat) krn kemungkinan muncul di
    token lain -- aman kalau tak pernah ketemu (count tetap 0).

    Eksperimen SEBELUMNYA (top_holder_tags() versi awal) nyoba field 'tags'
    tapi cuma sample 3 HOLDER TERBESAR per token -- semua kosong,
    disimpulkan keliru "field tak berguna". Itu BIAS SAMPLING: holder
    terbesar biasanya pool/whale lama, BUKAN representatif tail top-100
    tempat fresh-wallet farm/wash-trader biasanya nyebar -- makanya di sini
    di-scan SEMUA 100 baris.

    scam_risk_pct = MAX dari semua kategori individual (bukan dijumlah,
    supaya tak double-count wallet yg sama kena >1 tag) -- dipakai sbg
    sinyal ringkas "seberapa parah pola scam di top-100 token ini".

    bundler_cluster: 6 heuristik STATISTIK per permintaan user eksplisit
    (SOL balance/wallet age/bought avg mc/remaining supply/funding source/
    holding duration) -- lihat _bundler_cluster_signal(). market_cap_usd/
    price_usd/sol_price WAJIB diisi (dari Dexscreener/harga SOL yg sudah
    diambil main.py) supaya "bought avg mc" bisa dihitung; tanpa itu
    bundler_cluster.available=False (bukan crash, degrade sbg biasa).
    """
    price_now_sol = (price_usd / sol_price) if sol_price > 0 else 0.0
    out: Dict[str, Any] = {
        "available": False, "sample_count": 0, "coverage_pct": 0.0,
        "scam_risk_pct": 0.0,
        "is_new_pct": 0.0, "is_new_count": 0,
        "is_suspicious_pct": 0.0, "is_suspicious_count": 0,
    }
    for pct_key in _SCAM_PATTERN_TAGS.values():
        out[pct_key] = 0.0
        out[pct_key.replace("_pct", "_count")] = 0
    try:
        data = _get("/v1/market/token_top_holders", {"chain": chain, "address": mint, "limit": 100})
        if not data:
            return out
        rows = data if isinstance(data, list) else (
            data.get("list") or data.get("holders") or data.get("data") or []
        )
        if not rows:
            return out

        tag_counter: Dict[str, int] = {}
        tag_pct: Dict[str, float] = {k: 0.0 for k in _SCAM_PATTERN_TAGS}
        tag_n: Dict[str, int] = {k: 0 for k in _SCAM_PATTERN_TAGS}
        new_pct = susp_pct = coverage = 0.0
        new_n = susp_n = 0
        # -- Bundler-cluster (6 heuristik, per permintaan user, dari baris
        # WALLET ASLI saja -- addr_type==0. addr_type!=0 (verified live:
        # addr_type=2 exchange="pump_amm" = alamat POOL AMM, semua field
        # trading/funding-nya nol/kosong) DIKECUALIKAN, akan mencemari
        # analisa krn bukan wallet manusia. Lihat _bundler_cluster_signal().
        sol_bal_usd_list: List[float] = []
        wallet_age_days_list: List[float] = []
        buy_mc_usd_list: List[float] = []
        remaining_pct_list: List[float] = []
        holding_days_list: List[float] = []
        funding_counter: Dict[str, int] = {}
        funding_n = 0
        now_ts = time.time()
        for h in rows:
            if not isinstance(h, dict):
                continue
            pct = float(h.get("amount_percentage", 0) or 0) * 100.0
            coverage += pct
            tags = {str(t).strip().lower() for t in (h.get("tags") or [])}
            for t in tags:
                tag_counter[t] = tag_counter.get(t, 0) + 1
            for tag_name in _SCAM_PATTERN_TAGS:
                if tag_name in tags:
                    tag_pct[tag_name] += pct
                    tag_n[tag_name] += 1
            if h.get("is_new") is True:
                new_pct += pct
                new_n += 1
            if h.get("is_suspicious") is True:
                susp_pct += pct
                susp_n += 1

            if h.get("addr_type") == 0:
                remaining_pct_list.append(pct)
                try:
                    sol_bal_usd_list.append(float(h.get("native_balance", 0) or 0) / 1e9 * sol_price)
                except (TypeError, ValueError):
                    pass
                created_at = h.get("created_at")
                if created_at:
                    wallet_age_days_list.append((now_ts - float(created_at)) / 86400.0)
                start_h = h.get("start_holding_at")
                if start_h:
                    end_h = h.get("end_holding_at") or now_ts
                    holding_days_list.append((float(end_h) - float(start_h)) / 86400.0)
                avg_cost = h.get("avg_cost")
                if avg_cost and price_now_sol > 0 and market_cap_usd > 0:
                    buy_mc_usd_list.append(market_cap_usd * (float(avg_cost) / price_now_sol))
                from_addr = ((h.get("native_transfer") or {}).get("from_address") or "").strip()
                if from_addr:
                    funding_counter[from_addr] = funding_counter.get(from_addr, 0) + 1
                    funding_n += 1

        for tag_name, pct_key in _SCAM_PATTERN_TAGS.items():
            out[pct_key] = round(tag_pct[tag_name], 1)
            out[pct_key.replace("_pct", "_count")] = tag_n[tag_name]
        out.update({
            "available": True,
            "is_new_pct": round(new_pct, 1), "is_new_count": new_n,
            "is_suspicious_pct": round(susp_pct, 1), "is_suspicious_count": susp_n,
            "sample_count": len(rows), "coverage_pct": round(coverage, 1),
        })
        out["scam_risk_pct"] = round(max(
            [out[k] for k in _SCAM_PATTERN_TAGS.values()] + [new_pct, susp_pct]
        ), 1)
        out["bundler_cluster"] = _bundler_cluster_signal(
            sol_bal_usd_list, wallet_age_days_list, buy_mc_usd_list,
            remaining_pct_list, holding_days_list, funding_counter, funding_n,
        )
        log.info(
            "GMGN bundler_cluster utk mint %s...: %s",
            mint[:6], out["bundler_cluster"],
        )

        log.info(
            "GMGN top100 tag distribution (semua %d baris) utk mint %s...: %s",
            len(rows), mint[:6], tag_counter,
        )
        log.info(
            "GMGN top100_cluster_analysis OK utk mint %s...: scam_risk=%.1f%% "
            "(wash_trader=%.1f%% sandwich_bot=%.1f%% bundler=%.1f%% rat_trader=%.1f%% "
            "fresh=%.1f%% is_new=%.1f%% is_suspicious=%.1f%%) dari %d holder (coverage %.1f%% supply)",
            mint[:6], out["scam_risk_pct"], out["wash_trader_pct"], out["sandwich_bot_pct"],
            out["bundler_pct"], out["rat_trader_pct"], out["fresh_pct"], new_pct, susp_pct,
            len(rows), coverage,
        )
    except Exception as e:  # noqa: BLE001
        log.info("GMGN top100_cluster_analysis gagal utk mint %s...: %s (degrade)", mint[:6], e)
    return out
