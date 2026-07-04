"""
sources/helius.py — Data on-chain via Helius RPC/DAS (free tier).

Dipakai untuk:
  Stage 3 (keamanan kontrak):
    - getAsset -> mint_authority, freeze_authority, Token-2022 extensions (transfer fee)
  Stage 4 (distribusi holder):
    - getTokenLargestAccounts / getTokenAccounts -> top holder & share supply
    - getSignaturesForAddress -> umur/aktivitas wallet (deteksi fresh wallet)
    - getBalance -> saldo SOL wallet (deteksi wallet kosong)

Semua panggilan lewat JSON-RPC ke:
  https://mainnet.helius-rpc.com/?api-key=<KEY>

Semua fungsi degrade gracefully: return None / struktur kosong bila key tak ada
atau API mati, supaya pipeline tetap jalan (dan menandai ⚠️ ketimbang crash).
"""

import logging
from typing import Any, Dict, List, Optional

import config
from sources import http

log = logging.getLogger("helius")

LAMPORTS_PER_SOL = 1_000_000_000


def _rpc_url() -> Optional[str]:
    if not config.HELIUS_API_KEY:
        return None
    return f"https://mainnet.helius-rpc.com/?api-key={config.HELIUS_API_KEY}"


def _rpc(method: str, params: Any) -> Optional[Any]:
    """Panggilan JSON-RPC generik. Return 'result' atau None."""
    url = _rpc_url()
    if not url:
        log.warning("HELIUS_API_KEY kosong -> lewati %s", method)
        return None
    body = {"jsonrpc": "2.0", "id": "bot", "method": method, "params": params}
    data = http.post_json(url, json_body=body)
    if not data or "result" not in data:
        if data and "error" in data:
            log.info("Helius %s error: %s", method, data["error"])
        return None
    return data["result"]


# ---------------------------------------------------------------------------
# STAGE 3 — Keamanan kontrak
# ---------------------------------------------------------------------------
def get_security_info(mint: str) -> Optional[Dict[str, Any]]:
    """
    Kembalikan info keamanan mint:
      {
        mint_authority: str|None,
        freeze_authority: str|None,
        transfer_fee_bps: int,        # 0 kalau tak ada extension
        is_token_2022: bool,
        _available: bool              # apakah data berhasil diambil
      }
    """
    result = _rpc("getAsset", {"id": mint})
    if not result:
        return {"_available": False}

    # Struktur DAS: token_info berisi mint/freeze authority utk fungible.
    token_info = result.get("token_info") or {}
    mint_auth = token_info.get("mint_authority")
    freeze_auth = token_info.get("freeze_authority")

    # Deteksi Token-2022 + transfer fee via getAccountInfo (parsed) sebagai fallback,
    # karena getAsset tak selalu ekspos extensions.
    is_2022 = False
    transfer_fee_bps = 0
    acc = _rpc(
        "getAccountInfo",
        [mint, {"encoding": "jsonParsed"}],
    )
    try:
        val = (acc or {}).get("value") or {}
        owner = val.get("owner", "")
        is_2022 = owner == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
        parsed = ((val.get("data") or {}).get("parsed") or {}).get("info") or {}
        # authority juga bisa diambil dari sini (lebih andal utk beberapa mint)
        if mint_auth is None:
            mint_auth = parsed.get("mintAuthority")
        if freeze_auth is None:
            freeze_auth = parsed.get("freezeAuthority")
        for ext in parsed.get("extensions", []) or []:
            if ext.get("extension") == "transferFeeConfig":
                state = ext.get("state") or {}
                newer = state.get("newerTransferFee") or {}
                transfer_fee_bps = int(newer.get("transferFeeBasisPoints", 0) or 0)
    except Exception as e:  # noqa: BLE001
        log.debug("parse extensions gagal utk %s: %s", mint, e)

    return {
        "mint_authority": mint_auth,
        "freeze_authority": freeze_auth,
        "transfer_fee_bps": transfer_fee_bps,
        "is_token_2022": is_2022,
        "_available": True,
    }


# ---------------------------------------------------------------------------
# STAGE 4 — Distribusi holder
# ---------------------------------------------------------------------------
def get_top_holders(mint: str, limit: int) -> Optional[List[Dict[str, Any]]]:
    """
    Ambil daftar holder terbesar: [{owner|address, amount, ui_amount, pct}, ...].

    Strategi: getTokenLargestAccounts (cepat, top 20) dulu; kalau butuh lebih
    banyak dan tersedia, lengkapi supply share via getTokenSupply.
    Return None jika tak bisa diambil.
    """
    largest = _rpc("getTokenLargestAccounts", [mint])
    if not largest:
        return None
    accounts = largest.get("value") or []
    if not accounts:
        return None

    supply_info = _rpc("getTokenSupply", [mint])
    total_supply = 0.0
    try:
        total_supply = float(((supply_info or {}).get("value") or {}).get("uiAmount") or 0.0)
    except (TypeError, ValueError):
        total_supply = 0.0

    holders: List[Dict[str, Any]] = []
    for acc in accounts[:limit]:
        ui = float(acc.get("uiAmount") or 0.0)
        pct = (ui / total_supply * 100.0) if total_supply > 0 else 0.0
        holders.append(
            {
                "token_account": acc.get("address"),
                "ui_amount": ui,
                "pct": pct,
            }
        )
    return holders


def get_token_account_owner(token_account: str) -> Optional[str]:
    """Owner wallet dari sebuah token account (untuk analisa wallet age/balance)."""
    acc = _rpc("getAccountInfo", [token_account, {"encoding": "jsonParsed"}])
    try:
        info = (((acc or {}).get("value") or {}).get("data") or {}).get("parsed", {}).get("info", {})
        return info.get("owner")
    except Exception:  # noqa: BLE001
        return None


def get_wallet_activity(owner: str, limit: int = 25) -> Dict[str, Any]:
    """
    Heuristik umur/aktivitas wallet + proxy waktu "lahir" wallet -- TANPA call
    tambahan (dipakai jg utk deteksi cluster/bundle, lihat screening/holders.py).

    Return { tx_count_sample, is_fresh, earliest_seen_ts }.
    is_fresh = jumlah signature <= FRESH_WALLET_MAX_TXS (wallet baru/kosong aktivitas).
    earliest_seen_ts = blockTime tx TERLAMA dalam batch yg diambil (proxy "wallet
    dibuat kapan"). Untuk wallet fresh (< `limit` total tx), ini AKURAT (batch
    mencakup seluruh riwayatnya). Untuk wallet lama/aktif, ini cuma "setidaknya
    sudah ada sejak X" -- bukan genesis sungguhan, tapi cukup utk deteksi cluster
    krn bundler/sniper biasanya pakai wallet BARU yg dibuat sesaat sblm launch.
    """
    sigs = _rpc("getSignaturesForAddress", [owner, {"limit": limit}])
    if sigs is None:
        return {"tx_count_sample": None, "is_fresh": False, "earliest_seen_ts": None, "_available": False}
    n = len(sigs)
    earliest_seen_ts = None
    if sigs:
        block_times = [s.get("blockTime") for s in sigs if s.get("blockTime")]
        if block_times:
            earliest_seen_ts = min(block_times)
    return {
        "tx_count_sample": n,
        "is_fresh": n <= config.FRESH_WALLET_MAX_TXS,
        "earliest_seen_ts": earliest_seen_ts,
        "_available": True,
    }


def get_sol_balance_usd(owner: str, sol_price: float) -> Optional[float]:
    """Saldo SOL wallet dalam USD (untuk deteksi wallet kosong)."""
    res = _rpc("getBalance", [owner])
    try:
        lamports = (res or {}).get("value")
        if lamports is None:
            return None
        return lamports / LAMPORTS_PER_SOL * sol_price
    except Exception:  # noqa: BLE001
        return None
