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
