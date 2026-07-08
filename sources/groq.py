"""
sources/groq.py — Fallback sintesis narasi via Groq API gratis (dipanggil
HANYA kalau Gemini gagal/kena limit -- lihat main.py).

Kenapa Groq sbg fallback: rate limit gratisnya jauh lebih longgar (~30 RPM /
1.000 RPD per model llama-3.3-70b-versatile) dibanding Gemini, dan infra
resmi (bukan aggregator pihak ketiga) -- lihat diskusi di sesi ini. API-nya
format "OpenAI-compatible chat completions", beda dari Gemini generateContent,
tapi prinsip keamanan & skema outputnya SAMA (lihat sources/ai_common.py &
sources/gemini.py utk penjelasan lengkap kenapa ini aman dari prompt-injection
dan kenapa cuma soft-nudge, bukan hard gate).

Groq JSON mode (response_format=json_object) memaksa output JSON valid tapi
TIDAK menegakkan enum spt Gemini response_schema -- makanya validasi manual
di ai_common.validate() tetap wajib jadi lapisan pertahanan utama di sini.
"""

import json
import logging
from typing import Any, Dict

import config
from sources import ai_common, http

log = logging.getLogger("groq")

URL = "https://api.groq.com/openai/v1/chat/completions"


def assess_narrative(
    symbol: str,
    category: str,
    nar: Dict[str, Any],
    lp: Dict[str, Any],
    vol: Dict[str, Any],
    hold: Dict[str, Any],
    vwap: Dict[str, Any],
    jup: Dict[str, Any],
    vol_organic: Dict[str, Any] = None,
    is_new_ath: bool = False,
) -> Dict[str, Any]:
    """Return { available, authenticity, meme_context, thesis, score_multiplier } -- lihat gemini.py."""
    out = ai_common.empty_result()
    if not config.GROQ_NARRATIVE_ENABLED or not config.GROQ_API_KEY:
        return out

    evidence = ai_common.build_evidence_block(nar)
    context = ai_common.build_context_block(lp, vol, hold, nar, vwap, jup, vol_organic, is_new_ath)
    prompt = ai_common.build_prompt(symbol, category, evidence, context)

    try:
        body = {
            "model": config.GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 320,
        }
        headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}
        resp = http.post_json(URL, json_body=body, headers=headers, timeout=config.HTTP_TIMEOUT)
        if not resp:
            return out
        text = resp["choices"][0]["message"]["content"]
        parsed = json.loads(text)
        validated = ai_common.validate(parsed)
        if not validated:
            log.info("Groq respons tak sesuai skema utk $%s: %s", symbol, parsed)
            return out
        out.update(validated)
        log.info("Groq OK utk $%s: %s (x%.2f)", symbol, out["authenticity"], out["score_multiplier"])
    except Exception as e:  # noqa: BLE001
        log.info("Groq gagal utk $%s: %s (degrade)", symbol, e)
    return out
