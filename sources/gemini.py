"""
sources/gemini.py — Sintesis narasi via Gemini API gratis (Google AI Studio).

HANYA dipakai untuk memperhalus interpretasi teks kualitatif narasi (post
Reddit/artikel News yang sudah lolos filter _looks_crypto_related di
narrative.py) -- BUKAN untuk Stage keamanan/hard-gate mana pun. Filosofi:
gate rug/kontrak/holder harus tetap deterministik & auditable; LLM cuma
menambah "rasa" pada bagian yang memang sudah soft-score (narasi).

Kenapa aman dari prompt-injection:
  - Teks Reddit/News (konten publik TAK TEPERCAYA) dikirim ke model murni
    sbg DATA yang dikutip (diberi label eksplisit "KUTIPAN EKSTERNAL"),
    bukan sbg instruksi.
  - Output dipaksa JSON via response_schema (Gemini API) dgn enum terbatas
    -> kalaupun model "dibujuk" konten eksternal, hasil paling ekstrem cuma
    authenticity="organik" (skor tetap DIKALIKAN dlm rentang 0.6-1.0, bukan
    additif) pada satu komponen soft-score narasi (bobot kecil dari total).
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
            "enum": ["organik", "campuran", "terkoordinasi"],
        },
        "summary": {"type": "STRING"},
    },
    "required": ["authenticity", "summary"],
}


def assess_narrative(symbol: str, category: str, nar: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return { available, authenticity, summary, score_multiplier }.
    score_multiplier (0.6-1.0) dipakai KALIKAN nar['score'] yang sudah ada
    (rule-based) -- bukan gantikan, cuma nudge terbatas.
    """
    out = ai_common.empty_result()
    if not config.GEMINI_NARRATIVE_ENABLED or not config.GEMINI_API_KEY:
        return out

    evidence = ai_common.build_evidence_block(nar)
    prompt = ai_common.build_prompt(symbol, category, evidence)

    try:
        model = config.GEMINI_MODEL
        url = f"{BASE}/models/{model}:generateContent?key={config.GEMINI_API_KEY}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": _RESPONSE_SCHEMA,
                "temperature": 0.2,
                "maxOutputTokens": 200,
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
