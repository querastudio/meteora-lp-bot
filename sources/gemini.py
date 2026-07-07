"""
sources/gemini.py — Sintesis AI via Gemini API gratis (Google AI Studio).

Tiga bagian (lihat sources/ai_common.py utk detail lengkap):
  1. authenticity -- HANYA dari teks kualitatif narasi (post Reddit/artikel
     News/chat pump.fun yang sudah lolos filter _looks_crypto_related di
     narrative.py). Tetap memengaruhi skor narasi (multiplier 0.6-1.0),
     BUKAN utk Stage keamanan/hard-gate mana pun. Filosofi: gate rug/
     kontrak/holder harus tetap deterministik & auditable; LLM cuma
     menambah "rasa" pada bagian yang memang sudah soft-score (narasi).
  2. meme_context -- ringkasan naratif SINGKAT "token/meme ini tentang apa
     & kenapa dapat atensi" (AI-themed, utility, hewan lucu viral, dst),
     disintesis dari kutipan eksternal -- UTAMANYA chat pump.fun (suara
     komunitas resmi token itu sendiri, paling relevan drpd Reddit/News
     generik). PURE TEKS deskriptif, TIDAK menyentuh skor.
  3. thesis -- sintesis SEMUA metrik (LP/volatilitas/holder/narasi/VWAP/
     Jupiter) jadi 1-2 kalimat "gambaran besar" RISIKO, beda dari
     meme_context yg deskriptif. PURE TEKS, TIDAK menyentuh skor sama
     sekali -- murni buat dibaca user di notifikasi.

Kenapa aman dari prompt-injection:
  - Teks Reddit/News (konten publik TAK TEPERCAYA) dikirim ke model murni
    sbg DATA yang dikutip (diberi label eksplisit "KUTIPAN EKSTERNAL"),
    bukan sbg instruksi. Metrik lain (fee/TVL, holder, dst) adalah ANGKA
    hasil hitungan kita sendiri -- bukan teks eksternal, aman dikirim tanpa
    label khusus.
  - Output dipaksa JSON via response_schema (Gemini API) dgn enum terbatas
    -> kalaupun model "dibujuk" konten eksternal, hasil paling ekstrem cuma
    authenticity="organik" (skor tetap DIKALIKAN dlm rentang 0.6-1.0, bukan
    additif) pada satu komponen soft-score narasi (bobot kecil dari total),
    dan field thesis cuma teks tampilan (tak ada jalur balik ke skor).
    Tak bisa memengaruhi Stage 1-4 (hard gate) sama sekali.
  - Gagal / respons tak valid -> degrade gracefully (available=False, tak
    ada penyesuaian skor) -> main.py akan coba sources/groq.py sbg fallback,
    lalu ke rule-based biasa kalau keduanya gagal.

Free tier (Google AI Studio, tanpa kartu kredit): lihat GEMINI_MODEL di
config.py.
"""

import json
import logging
from typing import Any, Dict

import config
from sources import ai_common, http

log = logging.getLogger("gemini")

BASE = "https://generativelanguage.googleapis.com/v1beta"

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "authenticity": {
            "type": "STRING",
            "enum": ["organik", "campuran", "terkoordinasi", "tidak diketahui"],
        },
        "meme_context": {"type": "STRING"},
        "thesis": {"type": "STRING"},
    },
    "required": ["authenticity", "meme_context", "thesis"],
}


def assess_narrative(
    symbol: str,
    category: str,
    nar: Dict[str, Any],
    lp: Dict[str, Any],
    vol: Dict[str, Any],
    hold: Dict[str, Any],
    vwap: Dict[str, Any],
    jup: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Return { available, authenticity, meme_context, thesis, score_multiplier }.
    score_multiplier (0.6-1.0) dipakai KALIKAN nar['score'] yang sudah ada
    (rule-based) -- bukan gantikan, cuma nudge terbatas. meme_context & thesis
    PURE TEKS, tak memengaruhi skor apa pun.
    """
    out = ai_common.empty_result()
    if not config.GEMINI_NARRATIVE_ENABLED or not config.GEMINI_API_KEY:
        return out

    evidence = ai_common.build_evidence_block(nar)
    context = ai_common.build_context_block(lp, vol, hold, nar, vwap, jup)
    prompt = ai_common.build_prompt(symbol, category, evidence, context)

    try:
        model = config.GEMINI_MODEL
        url = f"{BASE}/models/{model}:generateContent?key={config.GEMINI_API_KEY}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": _RESPONSE_SCHEMA,
                "temperature": 0.2,
                "maxOutputTokens": 320,
            },
        }
        resp = http.post_json(url, json_body=body, timeout=config.HTTP_TIMEOUT)
        if not resp:
            return out
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        validated = ai_common.validate(parsed)
        if not validated:
            log.info("Gemini respons tak sesuai skema utk $%s: %s", symbol, parsed)
            return out
        out.update(validated)
        log.info("Gemini OK utk $%s: %s (x%.2f)", symbol, out["authenticity"], out["score_multiplier"])
    except Exception as e:  # noqa: BLE001
        log.info("Gemini gagal utk $%s: %s (degrade)", symbol, e)
    return out
