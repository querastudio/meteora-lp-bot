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
JSON mentah di docs publik):
  - token/security: field & threshold dikonfirmasi dari
    docs/workflow-token-due-diligence.md resmi mereka (is_honeypot,
    open_source, owner_renounced, renounced_mint, renounced_freeze_account,
    buy_tax/sell_tax, top_10_holder_rate, rug_ratio, sniper_count) --
    cukup yakin.
  - token/info (dev holding %) & market/token_top_holders (tag wallet):
    TIDAK ada contoh JSON resmi -- parsing di bawah DEFENSIF (coba
    beberapa nama field umum), degrade ke available=False + log raw
    respons kalau tak ketemu, supaya bisa diverifikasi/diperbaiki dari
    log run nyata tanpa nebak buta.

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


def token_security(mint: str, chain: str = "sol") -> Dict[str, Any]:
    """
    Return { available, is_honeypot, open_source, owner_renounced,
             renounced_mint, renounced_freeze, buy_tax, sell_tax,
             top10_holder_rate, rug_ratio, sniper_count, flags }.
    flags = peringatan siap-pakai (digabung ke `warnings` main.py).
    """
    out = {
        "available": False, "is_honeypot": False, "open_source": "",
        "owner_renounced": None, "renounced_mint": None, "renounced_freeze": None,
        "buy_tax": 0.0, "sell_tax": 0.0, "top10_holder_rate": 0.0,
        "rug_ratio": 0.0, "sniper_count": 0, "flags": [],
    }
    try:
        data = _get("/v1/token/security", {"chain": chain, "address": mint})
        if not data:
            return out
        d = data[0] if isinstance(data, list) and data else data
        if not isinstance(d, dict):
            return out
        # TEMPORARY: log raw response penuh spy field asli kelihatan (dugaan
        # nama/tipe field dari docs meleset -- lihat open_source=0 di log
        # produksi). Hapus/perkecil lagi setelah field dikonfirmasi.
        log.info("GMGN token/security RAW utk mint %s...: %s", mint[:6], str(d)[:2000])

        is_honeypot = str(d.get("is_honeypot", "no")).strip().lower() == "yes"
        open_source = str(d.get("open_source", "")).strip().lower()
        owner_renounced = str(d.get("owner_renounced", "")).strip().lower() == "yes"
        renounced_mint = bool(d.get("renounced_mint", False))
        renounced_freeze = bool(d.get("renounced_freeze_account", False))
        buy_tax = float(d.get("buy_tax", 0) or 0)
        sell_tax = float(d.get("sell_tax", 0) or 0)
        top10 = float(d.get("top_10_holder_rate", 0) or 0)
        rug_ratio = float(d.get("rug_ratio", 0) or 0)
        sniper_count = int(d.get("sniper_count", 0) or 0)

        flags: List[str] = []
        if is_honeypot:
            flags.append("GMGN: terdeteksi HONEYPOT")
        if open_source == "unknown":
            flags.append("GMGN: source code belum terverifikasi")
        if buy_tax > 0.10 or sell_tax > 0.10:
            flags.append(f"GMGN: tax tinggi (buy {buy_tax*100:.0f}% / sell {sell_tax*100:.0f}%)")
        if rug_ratio > 0.30:
            flags.append(f"GMGN: rug_ratio tinggi ({rug_ratio*100:.0f}%)")
        if sniper_count > 20:
            flags.append(f"GMGN: sniper count tinggi ({sniper_count})")

        out.update({
            "available": True, "is_honeypot": is_honeypot, "open_source": open_source,
            "owner_renounced": owner_renounced, "renounced_mint": renounced_mint,
            "renounced_freeze": renounced_freeze, "buy_tax": buy_tax, "sell_tax": sell_tax,
            "top10_holder_rate": round(top10, 3), "rug_ratio": round(rug_ratio, 3),
            "sniper_count": sniper_count, "flags": flags,
        })
        log.info(
            "GMGN token/security OK utk mint %s...: honeypot=%s open_source=%s rug_ratio=%.2f",
            mint[:6], is_honeypot, open_source, rug_ratio,
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

        if out["available"]:
            log.info("GMGN token/info OK utk mint %s...: dev_holding=%.1f%%", mint[:6], out["dev_holding_pct"])
        else:
            # TEMPORARY: log semua NAMA FIELD (bukan cuma value terpotong)
            # spy pasti kelihatan kandidat field dev-holding yang benar.
            log.info(
                "GMGN token/info: field dev_holding tak ditemukan utk mint %s... keys=%s raw=%s",
                mint[:6], sorted(d.keys()), str(d)[:2000],
            )
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

        # TEMPORARY: log struktur holder pertama (nama field + raw) spy tag
        # wallet asli kelihatan -- dugaan field "tags"/"wallet_tags" dkk blm
        # terkonfirmasi (semua persentase selalu 0.0% di produksi).
        if isinstance(rows[0], dict):
            log.info(
                "GMGN top_holders RAW (holder pertama) utk mint %s...: keys=%s raw=%s",
                mint[:6], sorted(rows[0].keys()), str(rows[0])[:1200],
            )

        smart = bundler = sniper = rat = 0.0
        for h in rows:
            if not isinstance(h, dict):
                continue
            pct = float(h.get("rate") or h.get("percentage") or h.get("pct") or h.get("share") or 0)
            if pct <= 1.0:
                pct *= 100.0
            tags = h.get("tags") or h.get("wallet_tags") or h.get("tag_list") or h.get("tag") or []
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
