"""
notify.py — Kirim notifikasi Telegram + format scannable + generator link manual.

Untuk hal yang TAK bisa diotomasi gratis (phishing-tag GMGN, cluster Bubblemaps,
data X/IG/TikTok/pump.fun) kita TIDAK scraping — kita sisipkan link siap-klik
dengan mint address ter-embed supaya user verifikasi manual di HP.

Pesan pakai HTML parse mode Telegram (aman & rapi di mobile).
"""

import html
import logging
from typing import Any, Dict, List
from urllib.parse import quote_plus

import config
from sources import http

log = logging.getLogger("notify")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"

VERDICT_EMOJI = {"STRONG": "🟢", "WATCH": "🟡", "SKIP": "🔴"}


# ---------------------------------------------------------------------------
# Generator link verifikasi manual (mint ter-embed)
# ---------------------------------------------------------------------------
def build_manual_links(mint: str, pool_addr: str, symbol: str) -> Dict[str, str]:
    q = quote_plus(f"{symbol} solana") if symbol and symbol != "?" else quote_plus(mint)
    # Cashtag ($TICKER) lebih presisi daripada search nama biasa -- ini format
    # resmi X untuk mengumpulkan semua post yang menyebut ticker sbg saham/token.
    cashtag = quote_plus(f"${symbol}") if symbol and symbol != "?" else quote_plus(mint)
    return {
        "Meteora": f"https://app.meteora.ag/dlmm/{pool_addr}",
        "GMGN": f"https://gmgn.ai/sol/token/{mint}",
        "Bubblemaps": f"https://app.bubblemaps.io/sol/token/{mint}",
        "SolScan": f"https://solscan.io/token/{mint}",
        "DexScreener": f"https://dexscreener.com/solana/{mint}",
        "RugCheck": f"https://rugcheck.xyz/tokens/{mint}",
        "pump.fun": f"https://pump.fun/{mint}",
        "X search": f"https://x.com/search?q={q}&f=live",
        "X Cashtag": f"https://x.com/search?q={cashtag}&src=cashtag_click&f=live",
        "X Community": f"https://x.com/search?q={q}&f=communities",
        "TikTok": f"https://www.tiktok.com/search?q={q}",
        "Instagram": f"https://www.instagram.com/explore/tags/{quote_plus((symbol or '').lstrip('$'))}/",
    }


def _link(label: str, url: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'


def _yn(ok: bool) -> str:
    return "✅" if ok else "❌"


# ---------------------------------------------------------------------------
# Format pesan
# ---------------------------------------------------------------------------
def format_message(ctx: Dict[str, Any]) -> str:
    """
    ctx berisi semua hasil stage. Rakit pesan scannable.
    Field yang dipakai: verdict, score, symbol, mint, pool, metrics, pool_data,
    security, holders, lp, vol, narrative, warnings, links.
    """
    v = ctx["verdict"]
    emoji = VERDICT_EMOJI.get(v, "⚪")
    sym = html.escape(ctx["symbol"])
    m = ctx["metrics"]
    p = ctx["pool_data"]
    sec = ctx["security"]
    h = ctx["holders"]
    lp = ctx["lp"]
    vol = ctx["vol"]
    nar = ctx["narrative"]
    links = ctx["links"]
    warns: List[str] = ctx.get("warnings", [])

    lines: List[str] = []
    lines.append(f"{emoji} <b>{v} — ${sym}</b>  <i>({ctx['score']:.0f}/100)</i>")
    lines.append(f"Pool: {_link(p['name'] or 'Meteora', links['Meteora'])}")
    lines.append("")

    # HARD GATES
    lines.append("📊 <b>HARD GATES</b> (otomatis)")
    lines.append(
        f"─ MCap ${_h(m['market_cap'])} {_yn(True)} | Vol24h ${_h(m['volume_h24'])} {_yn(True)}"
    )
    ath_info = ctx["ath_info"]
    if ath_info.get("cold_start"):
        ath_txt = "data awal (tren harga stabil/naik) ⚠️"
    elif ath_info.get("making_new_ath"):
        ath_txt = "mencetak baru ✅"
    else:
        ath_txt = "dekat ATH ✅"
    ath_src = ath_info.get("source", "")
    lines.append(f"─ ATH: {ath_txt} <i>({ath_src})</i>" if ath_src else f"─ ATH: {ath_txt}")
    lines.append(
        f"─ TVL ${_h(p['tvl_usd'])} | Bin {p['bin_step']} | Base {p['base_fee_pct']}% | "
        f"Quote {p.get('_quote_symbol','?')} ✅"
    )
    lines.append(f"─ Global fee {p.get('_cum_fee_sol', 0)} SOL ✅")
    tax_pct = (sec.get('transfer_fee_bps', 0) or 0) / 100.0
    lines.append(
        f"─ no-mint {_yn(sec.get('mint_authority') is None)} "
        f"no-freeze {_yn(sec.get('freeze_authority') is None)} "
        f"no-tax {_yn(tax_pct <= config.MAX_TRANSFER_FEE_BPS/100)} "
        f"LP-lock ⚠️"
    )
    if h.get("available"):
        lines.append(
            f"─ Top10: {h['top10_pct']}% {_yn(h['top10_gate_pass'])} | "
            f"Fresh top20: {h['fresh_count']} {'⚠️' if h['fresh_count'] else '✅'}"
        )
    else:
        lines.append("─ Holder: data tak tersedia ⚠️")
    lines.append("")

    # KUALITAS LP
    lines.append("💰 <b>KUALITAS LP</b>")
    fee_flag = "✅" if lp["fee_tvl_daily_pct"] >= config.FEE_TVL_DAILY_GOOD_PCT else "⚠️"
    vol_flag = "✅" if lp["vol_tvl"] >= config.VOL_TVL_GOOD_RATIO else "⚠️"
    est = " (est)" if lp.get("fee_estimated") else ""
    lines.append(
        f"─ Fee/TVL harian: {lp['fee_tvl_daily_pct']}%{est} {fee_flag} | "
        f"Vol/TVL: {lp['vol_tvl']}× {vol_flag}"
    )
    lines.append(f"─ Volatilitas: {vol['note']} {'✅' if not vol['vertical_death'] else '🔴'}")
    lines.append(f"─ Konsentrasi LP: {'sehat ✅' if lp['lp_conc_score']>=0.7 else 'sedang ⚠️'} (est)")
    if lp.get("pool_age_hours") is not None:
        lines.append(f"─ Umur pool: {lp['pool_age_hours']:.0f} jam")
    lines.append("")

    # NARASI
    if nar.get("label") != "OFF":
        lines.append("📈 <b>NARASI</b>")
        t = nar.get("trends", {})
        if t.get("available"):
            trend_txt = ("📈 naik" if t.get("rising") else "📉 turun")
            sustain = " (tahan)" if t.get("sustained") else ""
            lines.append(f"─ Google Trends 7d: {trend_txt}{sustain}")
        else:
            lines.append("─ Google Trends: n/a")
        yt = nar.get("youtube", {})
        if yt.get("available"):
            lines.append(f"─ YouTube: {yt['video_count']} video / {_h(yt['total_views'])} view (72j)")
        nw = nar.get("news", {})
        news_txt = f"{nw.get('article_count',0)} artikel" if nw.get("available") else "n/a"
        lines.append(f"─ News: {news_txt} | Kategori: {nar.get('category','?')} | Narasi: {nar.get('label')}")
        # X (Twitter) tak bisa di-API gratis -> sisipkan link cashtag & community
        # langsung di blok narasi (bukan cuma di baris link bawah) supaya user
        # cek "vibe" manual sebagai bagian dari due diligence narasi, bukan afterthought.
        lines.append(
            f"─ X (Twitter): {_link('Cashtag $'+sym, links['X Cashtag'])} | "
            f"{_link('Community', links['X Community'])} — cek manual ⚠️"
        )
        lines.append("")

    # Warnings ringkas
    if warns:
        lines.append("⚠️ <b>CATATAN:</b> " + "; ".join(html.escape(x) for x in warns[:4]))
        lines.append("")

    # LINK VERIFIKASI MANUAL
    lines.append("🔗 <b>VERIFIKASI MANUAL</b> (klik):")
    order = ["GMGN", "Bubblemaps", "RugCheck", "SolScan", "pump.fun", "X search", "TikTok", "Instagram"]
    row = " | ".join(_link(k, links[k]) for k in order if k in links)
    lines.append(row)

    return "\n".join(lines)


def _h(n: float) -> str:
    """Format angka besar jadi ringkas: 1.8M / 420K / 1.2K."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return f"{n:.0f}"


# ---------------------------------------------------------------------------
# Kirim ke Telegram
# ---------------------------------------------------------------------------
def send(text: str) -> bool:
    """Kirim pesan. Return True jika sukses / dry-run."""
    if config.DRY_RUN:
        log.info("[DRY_RUN] pesan:\n%s", text)
        return True
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID kosong -> tak bisa kirim")
        return False

    resp = http.post_json(
        TG_API.format(token=config.TELEGRAM_BOT_TOKEN),
        json_body={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )
    ok = bool(resp and resp.get("ok"))
    if not ok:
        log.error("Gagal kirim Telegram: %s", resp)
    return ok
