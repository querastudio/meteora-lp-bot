"""
screening/hard_filters.py — Stage 1-3 (hard gate). Gagal satu = SKIP, buang.

Filosofi cascade: murah -> mahal. Gugurkan sedini mungkin utk hemat rate limit.
  Stage 1: dari data pool Meteora (0 call tambahan)
  Stage 2: Dexscreener (1 call/token) — mcap & volume
  Stage 3: Helius (keamanan kontrak) — PALING kritis utk LP pasif

Setiap fungsi mengembalikan (passed: bool, reasons: list[str]) supaya logging
audit bisa menjelaskan token gugur di gate mana.
"""

import logging
from typing import Any, Dict, List, Tuple

import config

log = logging.getLogger("hard_filters")


# ---------------------------------------------------------------------------
# STAGE 1 — HARD FILTER POOL
# ---------------------------------------------------------------------------
def stage1_pool(pool: Dict[str, Any], sol_price: float) -> Tuple[bool, str, List[str]]:
    """
    Cek pool vs semua gate Stage 1. Return (passed, quote_symbol, reasons).
    quote_symbol dipakai stage berikut untuk tahu mint token dasar (bukan quote).
    """
    reasons: List[str] = []

    # Quote token wajib SOL / USDC. Tentukan mana sisi quote & mana token dasar.
    quote_sym = None
    if pool["mint_x"] in config.QUOTE_MINTS:
        quote_sym = config.QUOTE_MINTS[pool["mint_x"]]
    elif pool["mint_y"] in config.QUOTE_MINTS:
        quote_sym = config.QUOTE_MINTS[pool["mint_y"]]
    if quote_sym is None:
        return False, "", ["quote bukan SOL/USDC"]

    ok = True
    if pool["tvl_usd"] < config.MIN_TVL_USD:
        ok = False
        reasons.append(f"TVL ${pool['tvl_usd']:,.0f} < ${config.MIN_TVL_USD:,.0f}")
    if pool["base_fee_pct"] < config.MIN_BASE_FEE_PCT:
        ok = False
        reasons.append(f"base_fee {pool['base_fee_pct']}% < {config.MIN_BASE_FEE_PCT}%")
    if pool["bin_step"] < config.MIN_BIN_STEP:
        ok = False
        reasons.append(f"bin_step {pool['bin_step']} < {config.MIN_BIN_STEP}")

    # cumulative fee global (USD) dikonversi ke SOL utk dibanding threshold SOL.
    cum_fee_sol = (pool["cumulative_fee_usd"] / sol_price) if sol_price > 0 else 0.0
    if cum_fee_sol < config.MIN_CUMULATIVE_FEE_SOL:
        ok = False
        reasons.append(
            f"global fee {cum_fee_sol:.1f} SOL < {config.MIN_CUMULATIVE_FEE_SOL} SOL"
        )

    pool["_cum_fee_sol"] = round(cum_fee_sol, 1)
    pool["_quote_symbol"] = quote_sym
    return ok, quote_sym, reasons


def base_mint_of(pool: Dict[str, Any]) -> str:
    """Mint token DASAR (yang bukan quote SOL/USDC)."""
    if pool["mint_x"] in config.QUOTE_MINTS:
        return pool["mint_y"]
    return pool["mint_x"]


# ---------------------------------------------------------------------------
# STAGE 2 — HARD FILTER TOKEN (Dexscreener: mcap & volume)
# ---------------------------------------------------------------------------
def stage2_token(metrics: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Cek token vs gate mcap. Floor vol24h FLAT ($1M) SENGAJA DIHAPUS dari sini
    (per keputusan user, 8 Juli 2026) -- data live konfirmasi ini jadi
    bottleneck DOMINAN funnel (7/8 kandidat gugur Stage 2 murni krn vol24h,
    2 di antaranya cuma kurang <10% dari floor), sementara gate "Volume
    Organik" (Stage 2.5 di main.py -- rasio mcap:fee kumulatif proporsional)
    sudah jadi pemeriksa volume yg LEBIH TEPAT (adil utk mcap kecil maupun
    besar, drpd floor flat yg sama rata). MIN_VOLUME_H24_USD di config.py
    TETAP dipakai screening/volatility.py sbg soft-signal "volume tahan
    lama" (bukan hard gate lagi di sini).
    """
    reasons: List[str] = []
    ok = True

    if metrics["market_cap"] < config.MIN_MARKET_CAP_USD:
        ok = False
        reasons.append(f"mcap ${metrics['market_cap']:,.0f} < ${config.MIN_MARKET_CAP_USD:,.0f}")

    return ok, reasons


def stage2_volume_organic(mcap_usd: float, cum_fee_sol: float) -> Dict[str, Any]:
    """
    Rasio "volume ORGANIK & TINGGI" per rumus user: mcap:global_sol_fee sehat
    ~10.000:1 (mcap $100k <-> 10 SOL fee kumulatif). BEDA dari
    MIN_CUMULATIVE_FEE_SOL (Stage 1, floor flat) -- ini PROPORSIONAL thdp
    mcap, jadi adil utk token mcap besar (butuh fee lbh banyak) maupun kecil
    (floor flat 20 SOL bisa kelonggaran/keketatan salah utk keduanya).

    Return { pass, expected_fee_sol, actual_fee_sol, ratio_actual, reason }.
    ratio_actual = mcap_usd / cum_fee_sol (makin KECIL makin sehat -- makin
    dekat/di bawah target rasio 10.000:1-nya).
    """
    expected_fee_sol = mcap_usd / config.MCAP_TO_FEE_SOL_RATIO if config.MCAP_TO_FEE_SOL_RATIO > 0 else 0.0
    min_tolerated = expected_fee_sol / max(config.MCAP_TO_FEE_SOL_TOLERANCE, 1.0)
    ratio_actual = (mcap_usd / cum_fee_sol) if cum_fee_sol > 0 else float("inf")
    passed = cum_fee_sol >= min_tolerated
    reason = ""
    if not passed:
        reason = (
            f"fee {cum_fee_sol:.1f} SOL < {min_tolerated:.1f} SOL yg diharapkan "
            f"(mcap ${mcap_usd:,.0f} / rasio {config.MCAP_TO_FEE_SOL_RATIO:,.0f}, "
            f"toleransi {config.MCAP_TO_FEE_SOL_TOLERANCE}x) -- volume blm sepadan mcap"
        )
    return {
        "pass": passed,
        "expected_fee_sol": round(expected_fee_sol, 1),
        "actual_fee_sol": round(cum_fee_sol, 1),
        "ratio_actual": round(ratio_actual, 0) if ratio_actual != float("inf") else None,
        "ratio_target": config.MCAP_TO_FEE_SOL_RATIO,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# STAGE 3 — KEAMANAN KONTRAK (Helius) — PALING KRITIS
# ---------------------------------------------------------------------------
def stage3_security(sec: Dict[str, Any]) -> Tuple[bool, List[str], List[str]]:
    """
    Cek keamanan kontrak. Return (passed, hard_reasons, warnings).

    Hard SKIP jika: mint_authority != null, freeze_authority != null,
    atau transfer fee > MAX_TRANSFER_FEE_BPS.

    LP-lock tak bisa diverifikasi 100% gratis -> jadi WARNING (⚠️), bukan hard
    gate; scoring akan menurunkan skor & notif menandai perlu cek manual.
    """
    hard: List[str] = []
    warn: List[str] = []

    if not sec or not sec.get("_available"):
        # Tak bisa verifikasi keamanan = terlalu berisiko utk LP pasif -> SKIP.
        return False, ["data keamanan tak tersedia (Helius)"], []

    if sec.get("mint_authority") is not None:
        hard.append("mint_authority AKTIF (bisa cetak token)")
    if sec.get("freeze_authority") is not None:
        hard.append("freeze_authority AKTIF (bisa bekukan)")

    fee_bps = sec.get("transfer_fee_bps", 0) or 0
    if fee_bps > config.MAX_TRANSFER_FEE_BPS:
        hard.append(f"transfer tax {fee_bps/100:.2f}% > {config.MAX_TRANSFER_FEE_BPS/100:.2f}%")

    # LP-lock: tak terverifikasi otomatis -> selalu tandai perlu cek manual.
    warn.append("LP-lock belum terverifikasi otomatis — cek manual")

    passed = len(hard) == 0
    return passed, hard, warn
