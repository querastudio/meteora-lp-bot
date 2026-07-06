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


# ---------------------------------------------------------------------------
# Offset polling Telegram getUpdates (fitur "kirim CA, bot balas analisa").
# Disimpan di state (bukan var lokal) krn tiap run cron proses baru/exit --
# tanpa ini, update_id yang sama akan diproses ulang tiap run.
# ---------------------------------------------------------------------------
def get_telegram_offset(state: Dict[str, Any]) -> int:
    return int(state.get("telegram_offset", 0))


def set_telegram_offset(state: Dict[str, Any], offset: int) -> None:
    state["telegram_offset"] = offset


# ---------------------------------------------------------------------------
# Merge state (dipakai scan.yml step "Commit state" -- lihat CLI di bawah).
#
# Kenapa perlu ini: dua run cron bisa saling susul (workflow_dispatch/schedule
# checkout ref yang sudah dipin sejak di-queue -- lihat catatan di scan.yml),
# jadi saat run A mau commit balik state_data.json, remote bisa sudah maju
# duluan (dipush run B). git rebase/merge BERBASIS BARIS gampang KONFLIK di
# file JSON walau isinya semantically bisa digabung -- dan retry loop lama
# (`git pull --rebase ... || true`) menelan kegagalan itu lalu diam-diam
# kehilangan seluruh update run A (push jadi no-op "Everything up-to-date"
# tapi dilaporkan sukses). merge() ini menggabungkan di level JSON (union
# per-token) supaya race TAK PERNAH kehilangan data secara diam-diam.
# ---------------------------------------------------------------------------
def merge(remote: Dict[str, Any], local: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gabungkan state `remote` (origin, mungkin sudah diupdate run lain) dengan
    `local` (hasil run ini). Token yang HANYA disentuh salah satu sisi ->
    dipertahankan apa adanya (tak pernah hilang). Token yang disentuh KEDUA
    sisi (jarang -- 2 run beririsan token) -> per-field, ambil yang paling
    "maju" (riwayat harga terpanjang = paling banyak titik data terbaru;
    last_notified_ts terbesar = notifikasi paling baru).
    """
    merged = dict(remote)
    merged_tokens: Dict[str, Any] = dict(remote.get("tokens", {}))
    for mint, local_tok in (local.get("tokens") or {}).items():
        remote_tok = merged_tokens.get(mint)
        if not remote_tok:
            merged_tokens[mint] = local_tok
            continue
        merged_tok = dict(remote_tok)
        if len(local_tok.get("price_history", [])) >= len(remote_tok.get("price_history", [])):
            merged_tok["price_history"] = local_tok.get("price_history", [])
        if local_tok.get("symbol"):
            merged_tok["symbol"] = local_tok["symbol"]
        if local_tok.get("last_notified_ts", 0) >= remote_tok.get("last_notified_ts", 0):
            if "last_verdict" in local_tok:
                merged_tok["last_verdict"] = local_tok["last_verdict"]
            if "last_notified_ts" in local_tok:
                merged_tok["last_notified_ts"] = local_tok["last_notified_ts"]
        merged_tokens[mint] = merged_tok
    merged["tokens"] = merged_tokens
    # Offset getUpdates: counter monoton, jangan pernah mundur.
    merged["telegram_offset"] = max(
        int(remote.get("telegram_offset", 0)), int(local.get("telegram_offset", 0))
    )
    return merged


if __name__ == "__main__":
    # CLI dipakai scan.yml: `python state.py <path-json-lokal>` -- baca
    # state_data.json working-copy SAAT INI (harus sudah di-checkout fresh
    # dari origin sebagai "remote"), gabung dengan snapshot lokal hasil run
    # ini, tulis balik ke state_data.json (in-place, siap di-commit).
    import sys

    if len(sys.argv) != 2:
        print("Usage: python state.py <local_snapshot.json>", file=sys.stderr)
        sys.exit(1)

    remote_state = load()
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        local_state = json.load(f)
    save(merge(remote_state, local_state))
