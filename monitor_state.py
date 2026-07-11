"""
monitor_state.py — Persistensi posisi LP yang dipantau (/start /stop /list
/status), TERPISAH dari state_data.json (screening.state) krn skema & siklus
hidupnya beda total: ini per-pool POSISI AKTIF milik user (trailing-stop,
alert history), bukan per-token riwayat harga screening kandidat baru.

Pola desain SAMA PERSIS dgn state.py (commit-back JSON ke repo, merge level-
JSON saat race antar-run) -- lihat docstring state.py utk alasan lengkap.

Struktur file:
{
  "pools": {
     "<pool_address>": {
        "chat_id": str,
        "symbol": str, "pool_name": str,
        "trail_percent": float,
        "range_pct": float,          # proksi "keluar range" dari entry_price
        "entry_time": float,         # epoch detik
        "entry_tvl": float, "entry_price": float,
        "tvl_peak": float,
        "last_check_ts": float,
        "next_check_due_ts": float,  # polling adaptif -- lihat position_monitor.py
        "voltvl_history": [float,...],   # rolling window utk rata2 Vol/TVL
        "consecutive": {"tvl_stop": int, "voltvl": int, "composite": int},
        "last_alert_ts_by_type": {"<ALERT_TYPE>": float, ...},  # anti-spam dedup
        "last_alert_any_ts": float,  # utk polling adaptif "abis alert -> sesering mungkin"
        "prior_mint_authority": str|None, "prior_freeze_authority": str|None,
        "prior_lp_locked": bool|None,
        "stopped": bool,             # soft-delete (union-merge tak bisa hapus key -- lihat merge())
     }, ...
  },
  "updated_at": float
}
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import config

log = logging.getLogger("monitor_state")


def load() -> Dict[str, Any]:
    path = config.MONITOR_STATE_FILE
    if not os.path.exists(path):
        return {"pools": {}, "updated_at": 0.0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "pools" not in data:
            data["pools"] = {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Monitor state korup/tak terbaca (%s) -> mulai bersih", e)
        return {"pools": {}, "updated_at": 0.0}


def save(state: Dict[str, Any]) -> None:
    state["updated_at"] = time.time()
    path = config.MONITOR_STATE_FILE
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        log.error("Gagal simpan monitor state: %s", e)


def _new_pool_record(
    chat_id: str, mint: str, symbol: str, pool_name: str, trail_percent: float,
    range_pct: float, entry_tvl: float, entry_price: float,
    prior_mint_authority: Optional[str], prior_freeze_authority: Optional[str],
    prior_lp_locked: Optional[bool], security_baseline_confirmed: bool,
    lp_baseline_confirmed: bool,
) -> Dict[str, Any]:
    now = time.time()
    return {
        "chat_id": str(chat_id),
        "mint": mint,
        "symbol": symbol, "pool_name": pool_name,
        "trail_percent": float(trail_percent),
        "range_pct": float(range_pct),
        "entry_time": now,
        "entry_tvl": float(entry_tvl),
        "entry_price": float(entry_price),
        "tvl_peak": float(entry_tvl),
        "tvl_last": float(entry_tvl),
        "last_check_ts": now,
        "next_check_due_ts": now,  # boleh langsung dicek siklus berikutnya
        "voltvl_history": [],
        "consecutive": {"tvl_stop": 0, "voltvl": 0, "composite": 0},
        "last_alert_ts_by_type": {},
        "last_alert_any_ts": 0.0,
        # Baseline authority/LP-lock diisi SEJAK /start (bukan nunggu siklus
        # cron pertama) -- kalau kosong, ada jendela waktu antara /start dan
        # siklus cek pertama yg "buta" thd perubahan authority. *_baseline_
        # confirmed membedakan "None krn confirmed revoked/unlocked" vs "None
        # krn API blm pernah berhasil dibaca sama sekali" -- HANYA kasus
        # pertama yg boleh memicu alert reaktivasi (lihat position_monitor.py
        # evaluate_cycle).
        "prior_mint_authority": prior_mint_authority,
        "prior_freeze_authority": prior_freeze_authority,
        "prior_lp_locked": prior_lp_locked,
        "security_baseline_confirmed": security_baseline_confirmed,
        "lp_baseline_confirmed": lp_baseline_confirmed,
        "stopped": False,
    }


def add_pool(
    state: Dict[str, Any], pool_address: str, chat_id: str, mint: str, symbol: str,
    pool_name: str, trail_percent: float, range_pct: float,
    entry_tvl: float, entry_price: float,
    prior_mint_authority: Optional[str] = None, prior_freeze_authority: Optional[str] = None,
    prior_lp_locked: Optional[bool] = None, security_baseline_confirmed: bool = False,
    lp_baseline_confirmed: bool = False,
) -> Dict[str, Any]:
    """Mulai pantau pool baru (atau restart pool yg sebelumnya di-/stop --
    baseline entry di-reset total, spt posisi baru)."""
    rec = _new_pool_record(
        chat_id, mint, symbol, pool_name, trail_percent, range_pct, entry_tvl, entry_price,
        prior_mint_authority, prior_freeze_authority, prior_lp_locked,
        security_baseline_confirmed, lp_baseline_confirmed,
    )
    state["pools"][pool_address] = rec
    return rec


def stop_pool(state: Dict[str, Any], pool_address: str) -> bool:
    """Soft-delete: tandai stopped=True (BUKAN hapus key -- union-merge tak
    bisa propagate penghapusan key, lihat merge()). Return False kalau pool
    tak ditemukan/sudah stopped."""
    pool = state["pools"].get(pool_address)
    if not pool or pool.get("stopped"):
        return False
    pool["stopped"] = True
    pool["last_check_ts"] = time.time()
    return True


def get_pool(state: Dict[str, Any], pool_address: str) -> Optional[Dict[str, Any]]:
    pool = state["pools"].get(pool_address)
    if not pool or pool.get("stopped"):
        return None
    return pool


def list_active_pools(state: Dict[str, Any], chat_id: str) -> List[Tuple[str, Dict[str, Any]]]:
    return [
        (addr, pool) for addr, pool in state["pools"].items()
        if not pool.get("stopped") and str(pool.get("chat_id")) == str(chat_id)
    ]


def list_due_pools(state: Dict[str, Any], now: Optional[float] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """Pool aktif yg next_check_due_ts sudah lewat -- dipanggil position_monitor.py
    tiap siklus cron utk implementasi polling adaptif tanpa banyak jadwal cron."""
    now = now if now is not None else time.time()
    return [
        (addr, pool) for addr, pool in state["pools"].items()
        if not pool.get("stopped") and float(pool.get("next_check_due_ts", 0)) <= now
    ]


# ---------------------------------------------------------------------------
# Offset polling Telegram getUpdates -- OFFSET SENDIRI, terpisah dari
# state_data.json punya main.py (lihat sources/telegram_inbound.py:
# poll_commands vs poll_new_mints). Position monitor ini workflow/cron
# terpisah (monitor.yml) yg baca inbox Telegram yg SAMA scr independen --
# masing2 py bookmark offset sendiri, saling abaikan pesan yg bukan urusannya
# (command /start dst vs mint address polos) jadi tak saling ganggu.
# ---------------------------------------------------------------------------
def get_telegram_offset(state: Dict[str, Any]) -> int:
    return int(state.get("telegram_offset", 0))


def set_telegram_offset(state: Dict[str, Any], offset: int) -> None:
    state["telegram_offset"] = offset


# ---------------------------------------------------------------------------
# Merge state (pola sama persis state.py -- lihat docstring di sana utk
# alasan lengkap kenapa union-merge level-JSON, bukan git rebase/merge baris).
# ---------------------------------------------------------------------------
def merge(remote: Dict[str, Any], local: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gabungkan `remote` (origin, mungkin sudah diupdate run lain) dgn `local`
    (hasil run ini). Pool yg cuma disentuh salah satu sisi -> dipertahankan
    apa adanya. Pool yg disentuh KEDUA sisi (jarang) -> sisi dgn
    last_check_ts TERBARU jadi basis (itu yg beneran ngecek live data run
    ini), tapi tvl_peak & stopped tetap digabung terpisah spy TAK PERNAH
    "mundur" (peak turun / stop ke-undo) akibat race.
    """
    merged = dict(remote)
    merged_pools: Dict[str, Any] = dict(remote.get("pools", {}))
    for addr, local_pool in (local.get("pools") or {}).items():
        remote_pool = merged_pools.get(addr)
        if not remote_pool:
            merged_pools[addr] = local_pool
            continue
        # Sisi dgn cek TERBARU (last_check_ts) jadi basis field2 turunan-siklus
        # (consecutive counters, last_alert, voltvl_history) -- itu yg py data
        # live paling segar. tvl_peak & stopped digabung terpisah di bawah spy
        # tak ikut "mundur" kalau basis yg dipilih justru py angka lebih lama.
        newer = local_pool if local_pool.get("last_check_ts", 0) >= remote_pool.get("last_check_ts", 0) else remote_pool
        merged_pool = dict(newer)
        merged_pool["tvl_peak"] = max(
            float(remote_pool.get("tvl_peak", 0) or 0), float(local_pool.get("tvl_peak", 0) or 0)
        )
        merged_pool["stopped"] = bool(remote_pool.get("stopped")) or bool(local_pool.get("stopped"))
        merged_pool["last_alert_any_ts"] = max(
            float(remote_pool.get("last_alert_any_ts", 0) or 0), float(local_pool.get("last_alert_any_ts", 0) or 0)
        )
        # last_alert_ts_by_type: union per-key, ambil timestamp terbesar tiap type
        # (anti-spam dedup harus lihat pengiriman TERAKHIR dari kedua sisi).
        merged_alerts: Dict[str, float] = dict(remote_pool.get("last_alert_ts_by_type") or {})
        for k, v in (local_pool.get("last_alert_ts_by_type") or {}).items():
            merged_alerts[k] = max(float(merged_alerts.get(k, 0) or 0), float(v or 0))
        merged_pool["last_alert_ts_by_type"] = merged_alerts
        merged_pools[addr] = merged_pool
    merged["pools"] = merged_pools
    merged["telegram_offset"] = max(
        int(remote.get("telegram_offset", 0)), int(local.get("telegram_offset", 0))
    )
    return merged


if __name__ == "__main__":
    # CLI dipakai monitor.yml: `python monitor_state.py <path-json-lokal>` --
    # pola sama persis state.py (lihat scan.yml step "Commit state").
    import sys

    if len(sys.argv) != 2:
        print("Usage: python monitor_state.py <local_snapshot.json>", file=sys.stderr)
        sys.exit(1)

    remote_state = load()
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        local_state = json.load(f)
    save(merge(remote_state, local_state))
