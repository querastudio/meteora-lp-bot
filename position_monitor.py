"""
position_monitor.py — Pantau LP Meteora DLMM yang SUDAH dipegang user, deteksi
rugpull/slow-rug via trailing-stop TVL, collapse Vol/TVL, range breach, dan
perubahan authority/LP-lock. BEDA dari main.py (yang screening KANDIDAT BARU
utk di-LP) -- ini utk posisi yang SUDAH di-LP, dipantau via command Telegram
/start /stop /list /status. State terpisah (monitor_state.py), cron terpisah
(.github/workflows/monitor.yml), workflow_dispatch terpisah dari scan.yml.

Alur per run (cron 5 menit, monitor.yml):
  1. Load monitor_state (posisi aktif + offset Telegram sendiri).
  2. Poll command Telegram baru (/start /stop /list /status) -> proses & balas.
  3. Cek semua pool yang next_check_due_ts sudah lewat (polling ADAPTIF per
     pool -- lihat config.py MONITOR_POLL_MIN_*, bukan semua pool dicek tiap
     tick 5 menit).
  4. Evaluasi trigger, kirim alert Telegram (anti-spam dedup per-type, kecuali
     CRITICAL yang selalu kirim), simpan state.

Prinsip sama dgn main.py: degrade gracefully per-field (1 API mati != batal
1 pool, apalagi crash run), bukan all-or-nothing.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import config
import monitor_state
import notify
from screening import hard_filters
from sources import dexscreener, gmgn, helius, meteora, telegram_inbound

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("position_monitor")

# --- Tipe alert ---
ALERT_TVL_STOP = "TVL_TRAILING_STOP"
ALERT_VOLTVL_COLLAPSE = "VOLTVL_COLLAPSE"
ALERT_COMPOSITE = "SLOW_RUG_COMPOSITE"
ALERT_RANGE_BREACH = "RANGE_BREACH"
ALERT_AUTHORITY = "AUTHORITY_CHANGE"
ALERT_LP_INTEGRITY = "LP_INTEGRITY"

TIER_FAST = "FAST RUG"
TIER_SLOW = "SLOW RUG"
TIER_CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Snapshot: ambil data live 1 pool utk 1 siklus cek.
# ---------------------------------------------------------------------------
def fetch_snapshot(pool_address: str, mint: str) -> Dict[str, Any]:
    """
    Degrade PER-FIELD (bukan all-or-nothing) -- 1 API mati tak boleh
    membatalkan seluruh cek pool ini, cukup field terkait ditandai
    unavailable & trigger yang butuh field itu di-skip siklus ini (lihat
    evaluate_cycle -- counter TAK direset palsu krn data hilang).
    """
    snap: Dict[str, Any] = {
        "pool_available": False, "tvl_usd": 0.0, "pool_name": "",
        "price_available": False, "price_usd": 0.0,
        "volume_available": False, "volume_5m": 0.0, "volume_1h": 0.0,
        "security_available": False, "mint_authority": None, "freeze_authority": None,
        "lp_lock_available": False, "lp_locked": None,
    }
    pool = meteora.fetch_pool_by_address(pool_address)
    if pool:
        snap["pool_available"] = True
        snap["tvl_usd"] = pool["tvl_usd"]
        snap["pool_name"] = pool["name"]

    metrics = dexscreener.get_token_metrics(mint)
    if metrics:
        snap["price_available"] = True
        snap["price_usd"] = metrics["price_usd"]

    gm_vol = gmgn.volume_momentum(mint)
    if gm_vol.get("available"):
        snap["volume_available"] = True
        snap["volume_5m"] = gm_vol.get("volume_5m", 0.0)
        snap["volume_1h"] = gm_vol.get("volume_1h", 0.0)

    sec = helius.get_security_info(mint)
    if sec and sec.get("_available"):
        snap["security_available"] = True
        snap["mint_authority"] = sec.get("mint_authority")
        snap["freeze_authority"] = sec.get("freeze_authority")

    gm_sec = gmgn.token_security(mint)
    if gm_sec.get("available"):
        snap["lp_lock_available"] = True
        snap["lp_locked"] = gm_sec.get("lp_locked")

    return snap


def _vol_tvl_ratio(snap: Dict[str, Any]) -> Optional[float]:
    """Vol/TVL pakai volume 1 JAM (bukan 24h spt lp_quality.py) -- sengaja,
    biar collapse kedeteksi cepat (selaras tujuan "cut loss secepat
    mungkin"), 24h terlalu lag utk posisi yang lagi dipantau real-time."""
    if not snap["volume_available"] or snap["tvl_usd"] <= 0:
        return None
    return snap["volume_1h"] / snap["tvl_usd"]


# ---------------------------------------------------------------------------
# Evaluasi trigger 1 siklus. Mutasi pool_state in-place (peak, counter,
# baseline authority/lock) & return alert yang FIRING siklus ini (blm
# difilter anti-spam dedup -- itu tanggung jawab caller, lihat _check_one_pool).
# ---------------------------------------------------------------------------
def evaluate_cycle(pool_state: Dict[str, Any], snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []

    def fire(alert_type: str, tier: str, extra: Optional[List[str]] = None) -> None:
        alerts.append({"type": alert_type, "tier": tier, "extra_lines": extra or []})

    # ---- Trigger 5: authority/LP integrity (CRITICAL) ----
    # Baseline diisi SEJAK /start (lihat monitor_state.add_pool) -- prior
    # None hanya berarti "belum pernah lihat data" (baru /start & API sempat
    # gagal), BUKAN "authority pasti revoked", jadi transisi None->non-None
    # cuma dianggap "reaktivasi" kalau prior itu sendiri hasil BACAAN NYATA
    # yang sebelumnya None (revoked terverifikasi). Field unavailable siklus
    # ini -> skip perbandingan & JANGAN timpa baseline (degrade, bukan reset).
    if snap["security_available"]:
        prior_mint = pool_state.get("prior_mint_authority")
        prior_freeze = pool_state.get("prior_freeze_authority")
        now_mint = snap["mint_authority"]
        now_freeze = snap["freeze_authority"]
        # security_baseline_confirmed True HANYA kalau ADA bacaan sukses
        # sebelumnya (dari /start atau siklus lalu) -- itu yg bikin prior=None
        # berarti "confirmed revoked" (bukan "blm pernah kebaca"), jadi baru
        # boleh dibandingkan.
        if pool_state.get("security_baseline_confirmed"):
            if prior_mint is None and now_mint is not None:
                fire(ALERT_AUTHORITY, TIER_CRITICAL, [f"Mint authority AKTIF KEMBALI: {now_mint}"])
            if prior_freeze is None and now_freeze is not None:
                fire(ALERT_AUTHORITY, TIER_CRITICAL, [f"Freeze authority AKTIF KEMBALI: {now_freeze}"])
        pool_state["prior_mint_authority"] = now_mint
        pool_state["prior_freeze_authority"] = now_freeze
        pool_state["security_baseline_confirmed"] = True

    if snap["lp_lock_available"]:
        prior_lp = pool_state.get("prior_lp_locked")
        now_lp = snap["lp_locked"]
        if pool_state.get("lp_baseline_confirmed") and prior_lp is True and now_lp is False:
            fire(ALERT_LP_INTEGRITY, TIER_CRITICAL, ["Status LP berbalik dari TERKUNCI -> TIDAK terkunci"])
        pool_state["prior_lp_locked"] = now_lp
        pool_state["lp_baseline_confirmed"] = True

    # ---- Trigger 1: TVL trailing stop ----
    tvl_now = snap["tvl_usd"] if snap["pool_available"] else None
    if tvl_now is not None:
        pool_state["tvl_last"] = tvl_now  # dipakai /list (status ringkas tanpa live fetch)
        peak_before = float(pool_state.get("tvl_peak", 0.0) or 0.0)
        pool_state["tvl_peak"] = max(peak_before, tvl_now)
        trail_pct = float(pool_state.get("trail_percent", config.MONITOR_DEFAULT_TRAIL_PCT))
        stop_level = pool_state["tvl_peak"] * (1 - trail_pct / 100.0)
        tvl_stop_breach = tvl_now <= stop_level

        single_cycle_drop_pct = (
            (peak_before - tvl_now) / peak_before * 100.0 if peak_before > 0 else 0.0
        )
        is_fast_rug_pattern = (
            trail_pct <= config.MONITOR_FAST_RUG_MAX_TRAIL_PCT
            and single_cycle_drop_pct >= config.MONITOR_FAST_RUG_SINGLE_CYCLE_DROP_PCT
        )
        if tvl_stop_breach and is_fast_rug_pattern:
            fire(ALERT_TVL_STOP, TIER_FAST, [f"TVL anjlok {single_cycle_drop_pct:.0f}% dlm 1 siklus"])
            pool_state["consecutive"]["tvl_stop"] = 0
        elif tvl_stop_breach:
            pool_state["consecutive"]["tvl_stop"] = pool_state["consecutive"].get("tvl_stop", 0) + 1
            if pool_state["consecutive"]["tvl_stop"] >= config.MONITOR_TVL_STOP_CONFIRM_CYCLES:
                fire(ALERT_TVL_STOP, TIER_SLOW)
        else:
            pool_state["consecutive"]["tvl_stop"] = 0

    # ---- Trigger 2 & 3: Vol/TVL collapse (alone) & composite slow-rug ----
    # Data tak lengkap siklus ini (tvl/volume gagal fetch) -> SKIP evaluasi
    # sepenuhnya (jangan reset/increment counter palsu krn 1 API blip).
    ratio_now = _vol_tvl_ratio(snap)
    history: List[float] = pool_state.setdefault("voltvl_history", [])
    if tvl_now is not None and ratio_now is not None:
        rolling_avg = sum(history) / len(history) if len(history) >= 3 else None
        voltvl_collapse = rolling_avg is not None and ratio_now < config.MONITOR_VOLTVL_COLLAPSE_RATIO * rolling_avg
        history.append(ratio_now)
        if len(history) > 20:
            history[:] = history[-20:]

        tvl_declining = tvl_now < pool_state.get("tvl_peak", 0.0) * 0.98

        if rolling_avg is not None:
            if tvl_declining and voltvl_collapse:
                pool_state["consecutive"]["composite"] = pool_state["consecutive"].get("composite", 0) + 1
                if pool_state["consecutive"]["composite"] >= config.MONITOR_COMPOSITE_CONFIRM_CYCLES:
                    fire(ALERT_COMPOSITE, TIER_SLOW, [
                        f"Vol/TVL {ratio_now:.2f} vs rata2 {rolling_avg:.2f}, TVL turun dari puncak"
                    ])
            else:
                pool_state["consecutive"]["composite"] = 0

            if voltvl_collapse and not tvl_declining:
                pool_state["consecutive"]["voltvl"] = pool_state["consecutive"].get("voltvl", 0) + 1
                if pool_state["consecutive"]["voltvl"] >= config.MONITOR_VOLTVL_CONFIRM_CYCLES:
                    fire(ALERT_VOLTVL_COLLAPSE, TIER_SLOW, [
                        f"Vol/TVL {ratio_now:.2f} vs rata2 {rolling_avg:.2f}"
                    ])
            else:
                pool_state["consecutive"]["voltvl"] = 0
    # else: tvl/volume gagal fetch siklus ini -- JANGAN append history atau
    # reset/increment counter (degrade, tunggu siklus berikut dgn data lengkap).

    # ---- Trigger 4: range breach (proksi dari entry_price -- lihat catatan
    # config.MONITOR_DEFAULT_RANGE_PCT: kita cuma py pool_address, bukan
    # posisi NFT DLMM spesifik user, jadi "keluar range" = harga jatuh
    # >range_pct% dari entry, bukan bin range on-chain asli). Cuma cek sisi
    # BAWAH (selaras filosofi LP pasif proyek ini: proteksi downside, bukan
    # upside breach yang justru bagus buat LP).
    if snap["price_available"] and pool_state.get("entry_price", 0) > 0:
        range_pct = float(pool_state.get("range_pct", config.MONITOR_DEFAULT_RANGE_PCT))
        lower_bound = pool_state["entry_price"] * (1 - range_pct / 100.0)
        if snap["price_usd"] <= lower_bound:
            fire(ALERT_RANGE_BREACH, TIER_SLOW)

    return alerts


def _should_send(pool_state: Dict[str, Any], alert_type: str, tier: str, now: float) -> bool:
    """Anti-spam dedup per-type, KECUALI CRITICAL (selalu kirim -- indikasi rug aktif)."""
    if tier == TIER_CRITICAL:
        return True
    last_ts = float(pool_state.get("last_alert_ts_by_type", {}).get(alert_type, 0) or 0)
    return (now - last_ts) >= config.MONITOR_ALERT_DEDUP_MINUTES * 60


def _next_check_interval_minutes(pool_state: Dict[str, Any], now: float, fired_this_cycle: bool) -> int:
    """Polling adaptif per pool -- lihat config.py MONITOR_POLL_MIN_*."""
    if fired_this_cycle or (now - float(pool_state.get("last_alert_any_ts", 0) or 0)) < (
        config.MONITOR_ALERT_COOLDOWN_STABLE_HOURS * 3600
    ):
        return config.MONITOR_POLL_MIN_AFTER_ALERT
    age_hours = (now - float(pool_state.get("entry_time", now))) / 3600.0
    if age_hours < 1:
        return config.MONITOR_POLL_MIN_UNDER_1H
    if age_hours < 24:
        return config.MONITOR_POLL_MIN_1H_TO_24H
    return config.MONITOR_POLL_MIN_OVER_24H


def _check_one_pool(addr: str, pool_state: Dict[str, Any], now: float) -> int:
    mint = pool_state.get("mint", "")
    snap = fetch_snapshot(addr, mint)
    fired = evaluate_cycle(pool_state, snap)

    sent = 0
    for alert in fired:
        if not _should_send(pool_state, alert["type"], alert["tier"], now):
            log.info("Alert %s utk %s ditekan (anti-spam dedup)", alert["type"], addr[:6])
            continue
        text = notify.format_position_alert(addr, pool_state, snap, alert)
        if notify.send(text):
            sent += 1
            pool_state.setdefault("last_alert_ts_by_type", {})[alert["type"]] = now
            pool_state["last_alert_any_ts"] = now

    pool_state["last_check_ts"] = now
    pool_state["next_check_due_ts"] = now + _next_check_interval_minutes(pool_state, now, bool(fired)) * 60
    return sent


def run_cycle(mst: Dict[str, Any], now: Optional[float] = None) -> int:
    """1 siklus cek semua pool yang jatuh tempo. Return jumlah alert terkirim."""
    now = now if now is not None else time.time()
    due = monitor_state.list_due_pools(mst, now)
    sent = 0
    for addr, pool_state in due:
        try:
            sent += _check_one_pool(addr, pool_state, now)
        except Exception as e:  # noqa: BLE001 — 1 pool error != crash run
            log.exception("Error cek pool %s: %s", addr, e)
    return sent


# ---------------------------------------------------------------------------
# Command handlers (/start /stop /list /status)
# ---------------------------------------------------------------------------
def _resolve_pool_and_mint(pool_address: str) -> Tuple[Optional[Dict[str, Any]], str]:
    pool = meteora.fetch_pool_by_address(pool_address)
    if not pool:
        return None, ""
    return pool, hard_filters.base_mint_of(pool)


def handle_start(mst: Dict[str, Any], chat_id: str, args: List[str]) -> str:
    if not args:
        return "Format: /start <pool_address> [trail_percent]"
    pool_address = args[0]
    try:
        trail_percent = float(args[1]) if len(args) > 1 else config.MONITOR_DEFAULT_TRAIL_PCT
    except ValueError:
        return f"trail_percent tak valid: {args[1]}"

    pool, mint = _resolve_pool_and_mint(pool_address)
    if not pool:
        return (
            f"Pool tidak ditemukan di Meteora DLMM: {pool_address}\n"
            "(pastikan ini alamat POOL, bukan alamat token/mint)"
        )

    metrics = dexscreener.get_token_metrics(mint)
    symbol = metrics["symbol"] if metrics else "?"
    entry_price = metrics["price_usd"] if metrics else 0.0

    sec = helius.get_security_info(mint)
    security_confirmed = bool(sec and sec.get("_available"))
    prior_mint_auth = sec.get("mint_authority") if security_confirmed else None
    prior_freeze_auth = sec.get("freeze_authority") if security_confirmed else None
    gm_sec = gmgn.token_security(mint)
    lp_confirmed = bool(gm_sec.get("available"))
    prior_lp_locked = gm_sec.get("lp_locked") if lp_confirmed else None

    monitor_state.add_pool(
        mst, pool_address, chat_id, mint, symbol, pool.get("name", ""),
        trail_percent, config.MONITOR_DEFAULT_RANGE_PCT, pool["tvl_usd"], entry_price,
        prior_mint_auth, prior_freeze_auth, prior_lp_locked,
        security_confirmed, lp_confirmed,
    )
    log.info("Mulai pantau pool %s ($%s) trail=%.0f%%", pool_address[:8], symbol, trail_percent)
    return (
        f"✅ Mulai pantau ${symbol}\n"
        f"Pool: {pool.get('name','')} ({pool_address[:6]}...{pool_address[-4:]})\n"
        f"TVL entry: ${pool['tvl_usd']:,.0f}\n"
        f"Harga entry: {entry_price}\n"
        f"Trailing stop: {trail_percent:.0f}%"
    )


def handle_stop(mst: Dict[str, Any], chat_id: str, args: List[str]) -> str:
    if not args:
        return "Format: /stop <pool_address>"
    pool_address = args[0]
    pool = monitor_state.get_pool(mst, pool_address)
    if not pool or str(pool.get("chat_id")) != str(chat_id):
        return f"Pool tak sedang dipantau: {pool_address}"
    monitor_state.stop_pool(mst, pool_address)
    log.info("Stop pantau pool %s", pool_address[:8])
    return f"🛑 Berhenti pantau ${pool.get('symbol','?')} ({pool_address[:6]}...{pool_address[-4:]})"


def handle_list(mst: Dict[str, Any], chat_id: str) -> str:
    pools = monitor_state.list_active_pools(mst, chat_id)
    if not pools:
        return "Tak ada pool yang sedang dipantau. Pakai /start <pool_address> [trail_percent]."
    return notify.format_position_list(pools)


def handle_status(mst: Dict[str, Any], chat_id: str, args: List[str]) -> str:
    """On-demand, TANPA nunggu siklus cron -- fetch live SEKARANG, tapi
    read-only (tak mutasi consecutive counter/tvl_peak/next_check_due_ts --
    itu cuma boleh berubah dari siklus cron asli, biar konfirmasi N-siklus
    trigger 1-3 tak keganggu user iseng /status berkali-kali)."""
    if not args:
        return "Format: /status <pool_address>"
    pool_address = args[0]
    pool_state = monitor_state.get_pool(mst, pool_address)
    if not pool_state or str(pool_state.get("chat_id")) != str(chat_id):
        return f"Pool tak sedang dipantau: {pool_address}"
    snap = fetch_snapshot(pool_address, pool_state.get("mint", ""))
    return notify.format_position_status(pool_address, pool_state, snap)


def handle_commands(mst: Dict[str, Any], commands: List[Dict[str, Any]]) -> None:
    chat_id = config.TELEGRAM_CHAT_ID
    for cmd in commands:
        try:
            if cmd["cmd"] == "start":
                reply = handle_start(mst, chat_id, cmd["args"])
            elif cmd["cmd"] == "stop":
                reply = handle_stop(mst, chat_id, cmd["args"])
            elif cmd["cmd"] == "list":
                reply = handle_list(mst, chat_id)
            elif cmd["cmd"] == "status":
                reply = handle_status(mst, chat_id, cmd["args"])
            else:
                continue
            notify.send(reply)
        except Exception as e:  # noqa: BLE001 — 1 command error != crash run
            log.exception("Error proses command %s: %s", cmd, e)
            notify.send(f"⚠️ Error proses /{cmd.get('cmd','?')}: {e}")


# ---------------------------------------------------------------------------
def run() -> int:
    log.info("=== Position monitor run mulai ===")
    mst = monitor_state.load()

    offset = monitor_state.get_telegram_offset(mst)
    commands, next_offset = telegram_inbound.poll_commands(offset)
    monitor_state.set_telegram_offset(mst, next_offset)
    if commands:
        handle_commands(mst, commands)

    sent = run_cycle(mst)

    monitor_state.save(mst)
    log.info("=== Selesai. %d alert terkirim. ===", sent)
    return sent


if __name__ == "__main__":
    import sys
    try:
        run()
    except Exception as e:  # noqa: BLE001
        log.exception("Run gagal total: %s", e)
        sys.exit(1)
