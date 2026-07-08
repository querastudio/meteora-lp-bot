"""
sources/telegram_inbound.py — Polling Telegram getUpdates utk fitur "kirim CA
ke bot, bot balas analisa lengkap" (on-demand, mint apa pun -- bukan cuma
kandidat hasil auto-screening).

Kenapa POLLING, bukan webhook: proyek ini sengaja 100% GitHub Actions cron +
push-only Telegram (lihat README/config.py) -- tak ada server/endpoint HTTPS
yang bisa dipasangi webhook Telegram. Polling getUpdates disisipkan ke AWAL
cron 5-menit yang SUDAH ADA (main.py:run()) -- zero infra baru, trade-off-nya
delay balasan sampai ~5 menit (nunggu tick cron berikutnya), bukan instan.

Keamanan: HANYA proses pesan dari TELEGRAM_CHAT_ID yang sudah dikonfigurasi
(chat pemilik bot sendiri) -- bot ini TIDAK publik, jadi orang lain yang
kebetulan tahu username bot tak bisa memicu API call mahal (Helius, Gemini,
dst) dengan spam mint address sembarangan.
"""

import logging
import re
from typing import List, Tuple

import config
from sources import http

log = logging.getLogger("telegram_inbound")

TG_API = "https://api.telegram.org/bot{token}/getUpdates"

# Mint address Solana: base58 (tanpa 0, O, I, l), 32-44 karakter.
_MINT_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def poll_new_mints(offset: int) -> Tuple[List[str], int]:
    """
    Ambil pesan Telegram baru sejak `offset` (getUpdates offset -- lihat
    docs: harus > update_id tertinggi yang sudah diproses).

    Return (mints, next_offset):
      mints       = mint address valid dari chat yang sah (bisa kosong)
      next_offset = offset baru utk disimpan ke state (dihitung dari
                    update_id TERTINGGI yang DILIHAT -- terlepas dari chat
                    asal/valid tidaknya mint -- biar pesan lama/tak relevan
                    tak diproses berulang tiap run).

    Degrade gracefully: gagal/API mati -> ([], offset) -- tak crash run,
    cukup dicoba lagi run berikutnya.

    BUG NYATA (dilaporkan user, spam notif $VLED berulang-ulang): getUpdates
    Telegram TAK menganggap pesan "sudah dibaca" hanya krn kita LIHAT --
    server Telegram baru betulan berhenti mengirim ulang update itu setelah
    kita panggil getUpdates LAGI dgn offset LEBIH TINGGI (per docs resmi
    Telegram). Sebelum fix ini, konfirmasi itu BARU terjadi di run
    BERIKUTNYA (lewat next_offset yg disimpan ke state_data.json & di-push
    balik ke git). Kalau push state itu GAGAL (kontensi banyak run
    beruntun -- lihat scan.yml, DAN memang kejadian berkali-kali sesi ini
    krn testing manual bertubi-tubi), run berikutnya muat ULANG offset LAMA
    dari git, panggil getUpdates dgn offset itu lagi -> Telegram kirim ULANG
    pesan yg SAMA -> analyze_by_mint() jalan lagi -> balasan Telegram
    terkirim DUPLIKAT -- bisa berulang TERUS selama push state gagal.
    """
    mints: List[str] = []
    next_offset = offset
    if not config.TELEGRAM_BOT_TOKEN:
        return mints, next_offset
    try:
        resp = http.get_json(
            TG_API.format(token=config.TELEGRAM_BOT_TOKEN),
            params={"offset": offset, "timeout": 0},
            timeout=config.HTTP_TIMEOUT,
        )
        if not resp or not resp.get("ok"):
            return mints, next_offset

        for upd in resp.get("result", []):
            update_id = upd.get("update_id")
            if isinstance(update_id, int) and update_id >= next_offset:
                next_offset = update_id + 1

            msg = upd.get("message") or upd.get("channel_post") or {}
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            if not config.TELEGRAM_CHAT_ID or chat_id != str(config.TELEGRAM_CHAT_ID):
                continue  # bukan chat pemilik -- abaikan (cegah abuse)

            text = msg.get("text", "") or ""
            mints.extend(_MINT_RE.findall(text))

        if mints:
            log.info("Telegram: %d mint diminta utk analisa manual", len(mints))

        # KONFIRMASI KE TELEGRAM SEKARANG JUGA (dlm run yg sama), JANGAN
        # nunggu next_offset berhasil di-persist ke git -- lihat bug di
        # docstring atas. Panggilan kedua ini murah (limit=1, tak proses
        # apa pun hasilnya) tp bikin Telegram BENERAN berhenti kirim ulang
        # pesan yg sama, TERLEPAS dari sukses/gagalnya commit state_data.json
        # setelahnya.
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
    return mints, next_offset
