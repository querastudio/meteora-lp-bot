"""
sources/telegram_inbound.py — Polling Telegram getUpdates utk 2 fitur beda:

  1. "kirim CA ke bot, bot balas analisa lengkap" (main.py, offset di
     state_data.json) -- poll_new_mints().
  2. "/start /stop /list /status" position monitor (position_monitor.py,
     offset SENDIRI di monitor_state.json) -- poll_commands().

Kenapa DUA fungsi dgn offset TERPISAH, bukan satu: keduanya jalan dari
WORKFLOW CRON BERBEDA (scan.yml vs monitor.yml), proses TERPISAH, tak bisa
saling lihat memori masing2 -- masing2 py bookmark offset sendiri di file
state sendiri. Keduanya scan INBOX YANG SAMA scr independen (masing2 lihat
SEMUA pesan >= offset-nya sendiri), tapi masing2 cuma ambil yg relevan
buatnya & abaikan sisanya (poll_new_mints skip teks berbentuk command spy
"/start <mint> ..." tak ikut disalahartikan sbg "user paste mint polos utk
dianalisa manual"; poll_commands cuma ambil teks berbentuk command, abaikan
mint polos). Jadi tak ada cross-talk duplikat walau baca sumber sama.

Kenapa POLLING, bukan webhook: proyek ini sengaja 100% GitHub Actions cron +
push-only Telegram (lihat README/config.py) -- tak ada server/endpoint HTTPS
yang bisa dipasangi webhook Telegram. Polling getUpdates disisipkan ke AWAL
cron 5-menit yang SUDAH ADA -- zero infra baru, trade-off-nya delay balasan
sampai ~5 menit (nunggu tick cron berikutnya), bukan instan.

Keamanan: HANYA proses pesan dari TELEGRAM_CHAT_ID yang sudah dikonfigurasi
(chat pemilik bot sendiri) -- bot ini TIDAK publik, jadi orang lain yang
kebetulan tahu username bot tak bisa memicu API call mahal (Helius, Gemini,
dst) dengan spam mint address/command sembarangan.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import config
from sources import http

log = logging.getLogger("telegram_inbound")

TG_API = "https://api.telegram.org/bot{token}/getUpdates"

# Mint address Solana: base58 (tanpa 0, O, I, l), 32-44 karakter.
_MINT_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

# Command position-monitor: /start|/stop|/list|/status, boleh ada "@botname"
# (Telegram nempelin ini otomatis di grup), argumen dipisah whitespace.
_CMD_RE = re.compile(r"^/(start|stop|list|status)(?:@\w+)?(?:\s+(.*))?$", re.IGNORECASE)


def parse_command(text: str) -> Optional[Dict[str, Any]]:
    """Parse 1 baris pesan jadi {"cmd": "start"|"stop"|"list"|"status", "args": [...]}
    atau None kalau bukan command yg dikenali. Diekspor (dipakai poll_new_mints
    utk SKIP command dari ekstraksi mint, dan poll_commands utk ekstrak command)."""
    m = _CMD_RE.match(text.strip())
    if not m:
        return None
    cmd = m.group(1).lower()
    rest = (m.group(2) or "").strip()
    return {"cmd": cmd, "args": rest.split() if rest else []}


def _fetch_authorized_texts(offset: int) -> Tuple[List[str], int]:
    """
    Ambil teks pesan baru sejak `offset` dari chat pemilik yg sah SAJA.
    Return (texts, next_offset) -- lihat poll_new_mints/poll_commands utk
    gimana masing2 memparse `texts`.

    Degrade gracefully: gagal/API mati -> ([], offset) -- tak crash run,
    cukup dicoba lagi run berikutnya.

    BUG NYATA (dilaporkan user, spam notif $VLED berulang-ulang -- lihat git
    history): getUpdates Telegram TAK menganggap pesan "sudah dibaca" hanya
    krn kita LIHAT -- server Telegram baru betulan berhenti mengirim ulang
    update itu setelah kita panggil getUpdates LAGI dgn offset LEBIH TINGGI.
    Konfirmasi ini WAJIB terjadi DLM RUN YG SAMA (bukan nunggu next_offset
    berhasil persist ke git run berikutnya -- push state bisa gagal krn
    kontensi banyak run beruntun, lihat scan.yml/monitor.yml).
    """
    texts: List[str] = []
    next_offset = offset
    if not config.TELEGRAM_BOT_TOKEN:
        return texts, next_offset
    try:
        resp = http.get_json(
            TG_API.format(token=config.TELEGRAM_BOT_TOKEN),
            params={"offset": offset, "timeout": 0},
            timeout=config.HTTP_TIMEOUT,
        )
        if not resp or not resp.get("ok"):
            return texts, next_offset

        for upd in resp.get("result", []):
            update_id = upd.get("update_id")
            if isinstance(update_id, int) and update_id >= next_offset:
                next_offset = update_id + 1

            msg = upd.get("message") or upd.get("channel_post") or {}
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            if not config.TELEGRAM_CHAT_ID or chat_id != str(config.TELEGRAM_CHAT_ID):
                continue  # bukan chat pemilik -- abaikan (cegah abuse)

            text = msg.get("text", "") or ""
            if text:
                texts.append(text)

        # KONFIRMASI KE TELEGRAM SEKARANG JUGA (dlm run yg sama) -- lihat
        # docstring atas. Panggilan kedua ini murah (limit=1, tak proses apa
        # pun hasilnya) tp bikin Telegram BENERAN berhenti kirim ulang pesan
        # yg sama, TERLEPAS dari sukses/gagalnya commit state setelahnya.
        if next_offset != offset:
            try:
                http.get_json(
                    TG_API.format(token=config.TELEGRAM_BOT_TOKEN),
                    params={"offset": next_offset, "timeout": 0, "limit": 1},
                    timeout=config.HTTP_TIMEOUT,
                )
            except Exception as e:  # noqa: BLE001
                log.info("Konfirmasi offset Telegram gagal: %s (non-fatal)", e)
    except Exception as e:  # noqa: BLE001
        log.info("Polling Telegram getUpdates gagal: %s (degrade, coba lagi run berikutnya)", e)
    return texts, next_offset


def poll_new_mints(offset: int) -> Tuple[List[str], int]:
    """
    Return (mints, next_offset) -- mint address valid dari chat yang sah,
    dipakai fitur "kirim CA, bot balas analisa" (main.py, offset di
    state_data.json).

    Teks berbentuk command (/start dst) DI-SKIP dari ekstraksi mint --
    kalau tidak, pool_address di dalam "/start <pool_address> 15" bakal ikut
    kena regex mint & salah-trigger analyze_by_mint() jg (base58 32-44 char
    tak bisa dibedakan dari mint token cuma dari bentuknya).
    """
    texts, next_offset = _fetch_authorized_texts(offset)
    mints: List[str] = []
    for text in texts:
        if parse_command(text) is not None:
            continue
        mints.extend(_MINT_RE.findall(text))
    if mints:
        log.info("Telegram: %d mint diminta utk analisa manual", len(mints))
    return mints, next_offset


def poll_commands(offset: int) -> Tuple[List[Dict[str, Any]], int]:
    """
    Return (commands, next_offset) -- command /start /stop /list /status dari
    chat yang sah, dipakai position_monitor.py (offset SENDIRI di
    monitor_state.json, lihat docstring modul).
    """
    texts, next_offset = _fetch_authorized_texts(offset)
    commands: List[Dict[str, Any]] = []
    for text in texts:
        cmd = parse_command(text)
        if cmd:
            commands.append(cmd)
    if commands:
        log.info("Telegram: %d command position-monitor diterima", len(commands))
    return commands, next_offset
