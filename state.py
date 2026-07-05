"""
state.py — Persistensi antar-run (anti-duplikat + riwayat harga utk Stage 6).

Keputusan desain: simpan state sebagai file JSON yang DI-COMMIT balik ke repo
(bukan Actions cache/artifact). Alasan:
  - Deterministik & persisten: cache bisa evicted (7 hari / kapasitas) -> riwayat
    harga hilang -> estimasi volatilitas Stage 6 salah. Commit-back tak pernah hilang.
  - Audit trail: perubahan verdict terekam di git history.
  - File kecil (hanya token yang pernah lolos) -> commit ringan.
Trade-off: ada 1 commit "bot" tiap state berubah. Diredam dengan [skip ci] di
pesan commit + concurrency guard di workflow supaya run tak tumpang tindih.

Struktur file:
{
  "tokens": {
     "<mint>": {
        "price_history": [float,...], # beberapa titik terakhir (capped)
        "last_verdict": "STRONG"|"WATCH",
        "last_notified_ts": float,    # epoch detik
        "symbol": str
     }, ...
  },
  "updated_at": float
}
"""

import json
import logging
import os
import time
from typing import Any, Dict, List

import config

log = logging.getLogger("state")

_MAX_HISTORY = 400  # ~1.4 hari @5 menit; cukup utk estimasi volatilitas & cap ukuran file


def load() -> Dict[str, Any]:
    """Muat state dari file. Kalau tak ada / rusak -> state kosong."""
    path = config.STATE_FILE
    if not os.path.exists(path):
        return {"tokens": {}, "updated_at": 0.0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "tokens" not in data:
            data["tokens"] = {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.warning("State korup/tak terbaca (%s) -> mulai bersih", e)
        return {"tokens": {}, "updated_at": 0.0}


def save(state: Dict[str, Any]) -> None:
    """Tulis state ke file (atomik: tulis ke temp lalu rename)."""
    state["updated_at"] = time.time()
    path = config.STATE_FILE
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        log.error("Gagal simpan state: %s", e)


def record_price(state: Dict[str, Any], mint: str, price: float, symbol: str = "") -> None:
    """Catat harga terbaru ke riwayat (dipakai Stage 6 utk estimasi volume-tahan-lama)."""
    tok = state["tokens"].setdefault(mint, {"price_history": [], "symbol": symbol})

    hist: List[float] = tok.get("price_history", [])
    if price > 0:
        hist.append(round(price, 12))
        if len(hist) > _MAX_HISTORY:
            hist = hist[-_MAX_HISTORY:]
        tok["price_history"] = hist
    if symbol:
        tok["symbol"] = symbol


def get_price_history(state: Dict[str, Any], mint: str) -> List[float]:
    return list(state["tokens"].get(mint, {}).get("price_history", []))


def should_notify(state: Dict[str, Any], mint: str, verdict: str) -> bool:
    """
    Anti-duplikat: jangan kirim ulang pool yang sudah dinotif KECUALI:
      - status NAIK (WATCH -> STRONG), atau
      - sudah lewat cooldown (RENOTIFY_COOLDOWN_HOURS) utk verdict yang sama.
    """
    tok = state["tokens"].get(mint, {})
    last_verdict = tok.get("last_verdict")
    last_ts = float(tok.get("last_notified_ts", 0.0))

    rank = {"WATCH": 1, "STRONG": 2}
    # Upgrade status -> selalu kirim.
    if last_verdict and rank.get(verdict, 0) > rank.get(last_verdict, 0):
        return True
    # Belum pernah dinotif -> kirim.
    if not last_verdict:
        return True
    # Verdict sama / turun -> hanya kirim bila cooldown lewat.
    hours_since = (time.time() - last_ts) / 3600.0
    return hours_since >= config.RENOTIFY_COOLDOWN_HOURS


def mark_notified(state: Dict[str, Any], mint: str, verdict: str) -> None:
    tok = state["tokens"].setdefault(mint, {})
    tok["last_verdict"] = verdict
    tok["last_notified_ts"] = time.time()
