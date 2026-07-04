"""
screening/hard_filters.py — Stage 1-3 (hard gate). Gagal satu = SKIP, buang.

Filosofi cascade: murah -> mahal. Gugurkan sedini mungkin utk hemat rate limit.
  Stage 1: dari data pool Meteora (0 call tambahan)
  Stage 2: Dexscreener (1 call/token) — termasuk ATH gate (butuh state)
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
# STAGE 2 — HARD FILTER TOKEN (Dexscreener + ATH via state)
# ---------------------------------------------------------------------------
def stage2_token(
    metrics: Dict[str, Any], recorded_ath: float
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Cek token vs gate mcap/volume + ATH proximity.

    recorded_ath = harga tertinggi yang PERNAH kita catat di state untuk token ini.
    Token dianggap lolos ATH bila harga sekarang >= (1 - buffer) * ATH tercatat,
    ATAU sedang mencetak ATH baru (harga sekarang >= recorded_ath).

    PENTING (cold start): saat token BARU pertama kali diamati (belum ada
    recorded_ath di state), kita TIDAK BOLEH asal klaim "mencetak ATH baru" --
    itu cuma berarti "baru pertama kali kita lihat", bukan bukti harga di
    puncak. Untuk cold start, pakai price_change_h24/h6 (Dexscreener) sebagai
    proxy: kalau harga sedang turun konsisten di 2 window terakhir, GAGALKAN
    gate (jelas bukan ATH), jangan diloloskan.

    Catatan keterbatasan: ATH di sini adalah ATH-sejak-bot-mulai-mengamati
    (proxy gratis), bukan ATH sepanjang masa on-chain. Lihat README.
    """
    reasons: List[str] = []
    info: Dict[str, Any] = {}
    ok = True

    if metrics["market_cap"] < config.MIN_MARKET_CAP_USD:
        ok = False
        reasons.append(f"mcap ${metrics['market_cap']:,.0f} < ${config.MIN_MARKET_CAP_USD:,.0f}")
    if metrics["volume_h24"] < config.MIN_VOLUME_H24_USD:
        ok = False
        reasons.append(f"vol24h ${metrics['volume_h24']:,.0f} < ${config.MIN_VOLUME_H24_USD:,.0f}")

    price = metrics["price_usd"]
    chg_h24 = metrics.get("price_change_h24", 0.0)
    chg_h6 = metrics.get("price_change_h6", 0.0)
    cold_start = recorded_ath <= 0
    # Turun konsisten di 2 window terakhir -> jelas bukan lagi di puncak.
    declining = chg_h24 < 0 and chg_h6 < 0

    if cold_start:
        # Belum ada riwayat -> tak bisa klaim ATH baru begitu saja. Pakai tren
        # harga sbg sanity check: kalau sedang turun, gagalkan gate.
        making_new_ath = not declining
        near_ath = False
    else:
        making_new_ath = price >= recorded_ath
        near_ath = price >= (1 - config.ATH_PROXIMITY_PCT / 100.0) * recorded_ath

    info["making_new_ath"] = making_new_ath
    info["near_ath"] = near_ath
    info["cold_start"] = cold_start
    info["recorded_ath"] = recorded_ath
    info["new_ath_value"] = max(recorded_ath, price)  # nilai ATH terbaru utk disimpan

    # Gate ATH: harus mencetak baru ATAU dalam buffer dari ATH tercatat.
    if not (making_new_ath or near_ath):
        ok = False
        if cold_start:
            reasons.append(f"tren turun (h24 {chg_h24:.1f}%, h6 {chg_h6:.1f}%) -- data ATH baru dikumpulkan")
        else:
            drop = (1 - price / recorded_ath) * 100 if recorded_ath > 0 else 0
            reasons.append(f"jauh dari ATH (-{drop:.0f}%)")

    return ok, reasons, info


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
