"""
sources/gmgn.py — GMGN OpenAPI: keamanan token, dev holding %, dan tag wallet
(smart money/bundler/sniper/rat_trader) di top holder -- PELENGKAP due
diligence Stage 3/4 kita sendiri (Helius + heuristik cluster-waktu),
BUKAN pengganti. GMGN nge-tag wallet dari funding-source tracing ASLI
(mereka trace siapa danai wallet), bukan proxy waktu-pembuatan spt punya
kita -- lihat catatan keterbatasan di screening/holders.py.

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
  - token/info (dev holding %) & market/token_top_holders (tag wallet):
    parsing masih DEFENSIF (coba beberapa nama field kandidat), degrade
    ke available=False + log field/nilai relevan kalau tak ketemu, supaya
    bisa diverifikasi/diperbaiki dari log run nyata tanpa nebak buta.

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

_SMART_TAGS = {"smart_degen", "renowned", "smart_money", "smart_wallet"}
_BUNDLER_TAGS = {"bundler"}
_SNIPER_TAGS = {"sniper"}
_RAT_TAGS = {"rat_trader"}


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
    Return { available, dev_holding_pct }. Nama field API belum dikonfirmasi
    resmi (GMGN tak kasih contoh JSON) -- coba beberapa kandidat nama field
    umum; kalau tak ketemu, degrade ke available=False + log raw respons
    (dipotong) supaya bisa diverifikasi dari log run nyata.
    """
    out = {"available": False, "dev_holding_pct": 0.0}
    candidates = ("dev_holding_rate", "dev_holding_percentage", "creator_holding_rate", "dev_holding")
    try:
        data = _get("/v1/token/info", {"chain": chain, "address": mint})
        if not data:
            return out
        d = data[0] if isinstance(data, list) and data else data
        if not isinstance(d, dict):
            return out

        for key in candidates:
            if key in d and d[key] is not None:
                val = float(d[key])
                out["dev_holding_pct"] = round(val * 100.0 if val <= 1.0 else val, 1)
                out["available"] = True
                break

        # TEMPORARY: keys() run sebelumnya konfirmasi field 'dev' &
        # 'wallet_tags_stat' ADA di respons, tapi nilainya blm sempat
        # kelihatan (raw dump lama kepotong sebelum sampai situ secara
        # insertion-order). Log NILAI keduanya langsung (bukan dump seluruh
        # dict) spy pasti kelihatan tanpa terpotong.
        log.info(
            "GMGN token/info dev/wallet_tags_stat utk mint %s...: dev=%s wallet_tags_stat=%s",
            mint[:6], d.get("dev"), d.get("wallet_tags_stat"),
        )

        if out["available"]:
            log.info("GMGN token/info OK utk mint %s...: dev_holding=%.1f%%", mint[:6], out["dev_holding_pct"])
        else:
            log.info("GMGN token/info: field dev_holding (kandidat lama) tak ditemukan utk mint %s...", mint[:6])
    except Exception as e:  # noqa: BLE001
        log.info("GMGN token/info gagal utk mint %s...: %s (degrade)", mint[:6], e)
    return out


def top_holder_tags(mint: str, chain: str = "sol") -> Dict[str, Any]:
    """
    Return { available, smart_money_pct, bundler_pct, sniper_pct,
             rat_trader_pct, holder_count }. Agregasi % supply top-100
    holder per kategori tag wallet GMGN (funding-source tracing asli).
    Skema field belum dikonfirmasi resmi -- parsing defensif + log raw
    kalau baris holder tak punya struktur yg diharapkan.
    """
    out = {
        "available": False, "smart_money_pct": 0.0, "bundler_pct": 0.0,
        "sniper_pct": 0.0, "rat_trader_pct": 0.0, "holder_count": 0,
    }
    try:
        data = _get("/v1/market/token_top_holders", {"chain": chain, "address": mint, "limit": 100})
        if not data:
            return out
        rows = data if isinstance(data, list) else (
            data.get("list") or data.get("holders") or data.get("data") or []
        )
        if not rows:
            return out

        # Field % supply DIKONFIRMASI dari live run: "amount_percentage"
        # (rasio 0-1) -- bukan salah satu kandidat lama (rate/percentage/
        # pct/share), itulah kenapa semua kategori selalu 0.0% sebelumnya
        # (pct selalu jatuh ke default 0 utk SEMUA holder).
        #
        # TEMPORARY: log field tag 3 holder pertama (nilai asli, bukan cuma
        # nama key) -- keys() run sebelumnya kasih 3 kandidat (tags,
        # maker_token_tags, wallet_tag_v2) tapi baru wallet_tag_v2 yg
        # kelihatan isinya ("TOP1", bukan smart_degen/bundler/dst -- sepertinya
        # cuma label ranking, bukan tag yg kita cari).
        for i, h in enumerate(rows[:3]):
            if isinstance(h, dict):
                log.info(
                    "GMGN top_holders tag-fields (holder #%d) utk mint %s...: "
                    "tags=%s maker_token_tags=%s wallet_tag_v2=%s is_suspicious=%s is_new=%s",
                    i, mint[:6], h.get("tags"), h.get("maker_token_tags"),
                    h.get("wallet_tag_v2"), h.get("is_suspicious"), h.get("is_new"),
                )

        smart = bundler = sniper = rat = 0.0
        for h in rows:
            if not isinstance(h, dict):
                continue
            pct = float(
                h.get("amount_percentage") or h.get("rate") or h.get("percentage")
                or h.get("pct") or h.get("share") or 0
            )
            if pct <= 1.0:
                pct *= 100.0
            tags = h.get("tags") or h.get("maker_token_tags") or h.get("wallet_tags") or h.get("tag_list") or []
            if isinstance(tags, str):
                tags = [tags]
            tagset = {str(t).strip().lower() for t in tags}
            if tagset & _SMART_TAGS:
                smart += pct
            if tagset & _BUNDLER_TAGS:
                bundler += pct
            if tagset & _SNIPER_TAGS:
                sniper += pct
            if tagset & _RAT_TAGS:
                rat += pct

        out.update({
            "available": True, "smart_money_pct": round(smart, 1), "bundler_pct": round(bundler, 1),
            "sniper_pct": round(sniper, 1), "rat_trader_pct": round(rat, 1),
            "holder_count": len(rows),
        })
        log.info(
            "GMGN top_holders OK utk mint %s...: smart=%.1f%% bundler=%.1f%% sniper=%.1f%% rat=%.1f%% (n=%d)",
            mint[:6], smart, bundler, sniper, rat, len(rows),
        )
    except Exception as e:  # noqa: BLE001
        log.info("GMGN top_holders gagal utk mint %s...: %s (degrade)", mint[:6], e)
    return out
