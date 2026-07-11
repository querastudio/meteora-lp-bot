"""
sources/telegram_inbound.py — Polling Telegram getUpdates, SATU konsumen SAJA
(main.py/scan.yml) utk 2 fitur:

  1. "kirim CA ke bot, bot balas analisa lengkap" (mint polos).
  2. "/start /stop /list /status" position monitor (diteruskan ke
     position_monitor.handle_commands() -- lihat main.py:run()).

BUG NYATA (dilaporkan user, /start tak pernah dibalas position_monitor
walau monitor.yml sudah ditrigger manual): awalnya modul ini py DUA fungsi
poll TERPISAH dgn offset TERPISAH (poll_new_mints di state_data.json utk
main.py, poll_commands di monitor_state.json utk position_monitor.py),
dgn asumsi keduanya bisa scan inbox Telegram yg sama scr independen kayak
2 pembaca beda buku. ASUMSI ITU SALAH -- getUpdates Telegram cuma py SATU
posisi konfirmasi GLOBAL per bot token, bukan per-consumer/per-offset-
tersimpan. Begitu SATU poller (scan.yml, yg jalan rutin tiap 5 menit)
konfirmasi suatu update_id (panggilan kedua dgn offset lebih tinggi),
Telegram BERHENTI TOTAL mengirim update itu ke SIAPA PUN -- termasuk
monitor.yml yg offset tersimpannya sendiri msh lebih rendah & belum pernah
"lihat" pesan itu dari sudut pandang file state-nya sendiri. Hasilnya:
command /start dkk raib begitu saja krn keburu "dimakan" scan.yml (yg
skip command dr ekstraksi mint, jd secara efektif membuang pesan itu)
SEBELUM monitor.yml (yg cron-nya jg blm tentu jalan tepat waktu) sempat
proses.

FIX: SATU poller saja (poll_updates(), dipanggil main.py/scan.yml --
cron paling reliable, sudah terverifikasi jalan rutin via investigasi
log GHOSTI/TRIPLET 11 Juli 2026), py offset TUNGGAL (state_data.json).
main.py meneruskan command hasil parse ke position_monitor.handle_commands()
LANGSUNG dlm proses yg sama (import, bukan lewat Telegram lagi) --
monitor.yml (cron terpisah) HANYA jalankan run_cycle() (evaluasi
trigger pool yg sudah dipantau), TAK PERNAH poll Telegram sendiri lagi.

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
    atau None kalau bukan command yg dikenali.

    BUG NYATA (dilaporkan user, /start jatuh ke "analisa manual" alih2
    kepakai position_monitor): Telegram nempelin LINK PREVIEW/baris tambahan
    ke `text` kalau pesan mengandung URL atau pesan multi-baris (mis. user
    paste "/start <pool_address>" lalu enter/paste link DexScreener di baris
    berikutnya). `_CMD_RE` lama pakai anchor `$` yg di mode non-MULTILINE
    HARUS reach absolute end-of-string, padahal `.` tak match `\n` --
    begitu ada BARIS KEDUA apa pun, seluruh match GAGAL diam2 (bukan raise
    error), balik None, lalu teks itu jatuh ke jalur _MINT_RE lama (nemu
    mint di baris manapun) -> analyze_by_mint() salah kepanggil. Fix: cuma
    parse BARIS PERTAMA thd command, abaikan baris sisanya.
    """
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    m = _CMD_RE.match(first_line)
    if not m:
        return None
    cmd = m.group(1).lower()
    rest = (m.group(2) or "").strip()
    return {"cmd": cmd, "args": rest.split() if rest else []}


def poll_updates(offset: int) -> Tuple[List[str], List[Dict[str, Any]], int]:
    """
    SATU-SATUNYA fungsi yang boleh memanggil Telegram getUpdates dgn
    konfirmasi offset (lihat docstring modul kenapa cuma boleh 1 konsumen).
    Return (mints, commands, next_offset):
      mints    = mint address polos (bukan command) dari chat yg sah, utk
                 fitur "kirim CA, bot balas analisa".
      commands = {"cmd": ..., "args": [...]} dari chat yg sah, diteruskan
                 main.py ke position_monitor.handle_commands().

    Degrade gracefully: gagal/API mati -> ([], [], offset) -- tak crash run,
    cukup dicoba lagi run berikutnya.

    BUG NYATA LAMA (dilaporkan user, spam notif $VLED berulang-ulang):
    getUpdates Telegram TAK menganggap pesan "sudah dibaca" hanya krn kita
    LIHAT -- server Telegram baru betulan berhenti mengirim ulang update itu
    setelah kita panggil getUpdates LAGI dgn offset LEBIH TINGGI. Konfirmasi
    ini WAJIB terjadi DLM RUN YG SAMA (bukan nunggu next_offset berhasil
    persist ke git run berikutnya -- push state bisa gagal krn kontensi
    banyak run beruntun).
    """
    mints: List[str] = []
    commands: List[Dict[str, Any]] = []
    next_offset = offset
    if not config.TELEGRAM_BOT_TOKEN:
        return mints, commands, next_offset
    try:
        resp = http.get_json(
            TG_API.format(token=config.TELEGRAM_BOT_TOKEN),
            params={"offset": offset, "timeout": 0},
            timeout=config.HTTP_TIMEOUT,
        )
        if not resp or not resp.get("ok"):
            return mints, commands, next_offset

        for upd in resp.get("result", []):
            update_id = upd.get("update_id")
            if isinstance(update_id, int) and update_id >= next_offset:
                next_offset = update_id + 1

            msg = upd.get("message") or upd.get("channel_post") or {}
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            if not config.TELEGRAM_CHAT_ID or chat_id != str(config.TELEGRAM_CHAT_ID):
                continue  # bukan chat pemilik -- abaikan (cegah abuse)

            text = msg.get("text", "") or ""
            if not text:
                continue
            cmd = parse_command(text)
            if cmd:
                commands.append(cmd)
            else:
                mints.extend(_MINT_RE.findall(text))

        if mints:
            log.info("Telegram: %d mint diminta utk analisa manual", len(mints))
        if commands:
            log.info("Telegram: %d command position-monitor diterima", len(commands))

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
    return mints, commands, next_offset
