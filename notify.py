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
        # Bot/web analisis cluster-bundle pihak-ketiga (tak ada API gratis,
        # jadi cuma link -- paste mint address manual setelah buka).
        "DevsNightmare": "https://t.me/soldevnightmarebot",
        "Deepnets": "https://deepnets.ai",
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
    vwap = ctx.get("vwap", {})
    lc = ctx.get("lunarcrush", {})
    jup = ctx.get("jupiter", {})
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
        lines.append(f"─ Top10: {h['top10_pct']}% {_yn(h['top10_gate_pass'])}")
        coord = h.get("coordination_label", "n/a")
        coord_emoji = {"TINGGI": "🔴", "SEDANG": "🟡", "WAJAR": "✅"}.get(coord, "")
        lines.append(f"─ Indikasi coordinated trading: {coord_emoji} {coord}")
        cluster_pct = h.get("largest_cluster_pct", 0.0)
        cluster_n = h.get("largest_cluster_wallets", 0)
        if cluster_n >= 2:
            lines.append(
                f"─ Cluster terbesar: {cluster_pct}% supply / {cluster_n} wallet "
                f"{_yn(h.get('cluster_gate_pass', True))} <i>(proxy waktu, bukan exact spt GMGN)</i>"
            )
        else:
            lines.append("─ Cluster: tak terdeteksi wallet berdekatan ✅")
    else:
        lines.append("─ Holder: data tak tersedia ⚠️")
    if jup.get("available"):
        label = jup.get("organic_label") or "?"
        jup_emoji = {"high": "✅", "medium": "🟡", "low": "🔴"}.get(label, "")
        lines.append(
            f"─ Jupiter Organic Score: {jup.get('organic_score',0):.0f}/100 "
            f"({label}) {jup_emoji} <i>(volume asli vs bot/wash-trading)</i>"
        )
    lines.extend(_gmgn_lines(ctx.get("gmgn", {})))
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
    if vwap.get("available"):
        pct = vwap.get("ratio_pct", 0.0)
        pos = "di atas" if vwap.get("above_vwap") else "di bawah"
        flag = "✅" if vwap.get("above_vwap") else "⚠️"
        extreme = " (ekstrem, waspada blow-off-top)" if pct > 200 else ""
        lines.append(
            f"─ VWAP (1j, sejak pool dibuat): harga {pos} VWAP {abs(pct):.0f}% {flag}{extreme}"
        )
    else:
        lines.append("─ VWAP: n/a")
    lines.append(f"─ Konsentrasi LP: {'sehat ✅' if lp['lp_conc_score']>=0.7 else 'sedang ⚠️'} (est)")
    if lp.get("pool_age_hours") is not None:
        lines.append(f"─ Umur pool: {lp['pool_age_hours']:.0f} jam")
    lines.append("")

    # NARASI — dipecah 2 sumbu: VIRALITAS (breadth+volume+diversitas komunitas)
    # vs DAYA TAHAN (masih hidup beberapa hari, bukan cuma spike sesaat).
    lines.extend(_narrative_lines(nar, lc, links, sym))

    # Warnings ringkas
    if warns:
        lines.append("⚠️ <b>CATATAN:</b> " + "; ".join(html.escape(x) for x in warns[:4]))
        lines.append("")

    # LINK VERIFIKASI MANUAL
    lines.append("🔗 <b>VERIFIKASI MANUAL</b> (klik):")
    order = [
        "GMGN", "Bubblemaps", "DevsNightmare", "Deepnets", "RugCheck", "SolScan",
        "pump.fun", "X search", "TikTok", "Instagram",
    ]
    row = " | ".join(_link(k, links[k]) for k in order if k in links)
    lines.append(row)

    return "\n".join(lines)


def _narrative_lines(nar: Dict[str, Any], lc: Dict[str, Any], links: Dict[str, str], sym: str) -> List[str]:
    """Blok NARASI (dipakai format_message & format_manual_message -- identik)."""
    lines: List[str] = []
    if nar.get("viral_label") == "OFF":
        return lines

    lines.append(
        f"📈 <b>NARASI</b> — Viralitas: {nar.get('viral_label','?')} | "
        f"Daya Tahan: {nar.get('durability_label','?')}"
    )
    lines.append(f"─ Kategori: {nar.get('category','?')}")

    t = nar.get("trends", {})
    if t.get("available"):
        trend_txt = "📈 naik" if t.get("rising") else "📉 turun"
        sustain = " (blm anjlok)" if t.get("sustained") else " (sudah anjlok)"
        lines.append(f"─ Google Trends 7d: {trend_txt}{sustain}, avg={t.get('avg',0)}")
    else:
        lines.append("─ Google Trends: n/a")

    yt = nar.get("youtube", {})
    if yt.get("available"):
        lines.append(
            f"─ YouTube: {yt.get('video_count',0)} video / {_h(yt.get('total_views',0))} view "
            f"/ {yt.get('channel_count',0)} channel berbeda (72j)"
        )
    else:
        lines.append("─ YouTube: n/a (butuh YOUTUBE_API_KEY)")

    rd = nar.get("reddit", {})
    if rd.get("available"):
        fresh = "✅ msh ada post baru 24j" if rd.get("posts_last24h", 0) > 0 else "⚠️ tak ada post baru 24j"
        lines.append(
            f"─ Reddit: {rd.get('post_count',0)} post / {_h(rd.get('total_score',0))} upvote "
            f"/ {rd.get('subreddit_count',0)} subreddit berbeda ({fresh})"
        )
    else:
        lines.append("─ Reddit: n/a")

    nw = nar.get("news", {})
    if nw.get("available"):
        lines.append(f"─ News: {nw.get('article_count',0)} artikel dari {nw.get('domain_count',0)} domain berbeda")
    else:
        lines.append("─ News: n/a")

    pf = nar.get("pumpfun", {})
    if pf.get("available"):
        fresh = "✅ msh ada pesan baru 24j" if pf.get("posts_last24h", 0) > 0 else "⚠️ tak ada pesan baru 24j"
        lines.append(
            f"─ Chat pump.fun: {pf.get('post_count',0)} pesan / {pf.get('member_count',0)} member / "
            f"{pf.get('distinct_posters',0)} wallet unik posting ({fresh})"
        )
    elif config.PUMPFUN_COMMUNITY_ENABLED and config.PUMPFUN_COMMUNITY_API_KEY:
        lines.append("─ Chat pump.fun: n/a (belum ada community/pesan utk token ini)")
    else:
        lines.append("─ Chat pump.fun: n/a (butuh PUMPFUN_COMMUNITY_API_KEY)")

    if lc.get("available"):
        lines.append(
            f"─ LunarCrush: Galaxy Score {lc.get('galaxy_score',0):.0f}/100, "
            f"sentiment {lc.get('sentiment_pct',0):.0f}% positif, "
            f"{lc.get('num_contributors',0)} kontributor (24j)"
        )
    elif config.LUNARCRUSH_ENABLED and config.LUNARCRUSH_API_KEY:
        lines.append("─ LunarCrush: n/a (belum ter-index -- wajar utk token baru)")

    # Insight kualitatif otomatis (rule-based dari kombinasi angka di atas).
    for insight in nar.get("insights", [])[:3]:
        lines.append(f"  💡 {html.escape(insight)}")

    # Konteks: kutipan ASLI (bukan karangan) dari post/artikel paling relevan
    # -- ini "penjelasan mengenai tokennya" (siapa/apa yg dibahas), diambil
    # dari data nyata, bukan sinopsis otomatis yang bisa salah/mengarang.
    evidence = nar.get("evidence", [])
    if evidence:
        lines.append("─ <b>Konteks</b> (kutipan asli):")
        for ev in evidence:
            src_txt = html.escape(ev["source"])
            if ev.get("url"):
                lines.append(f"  📝 {_link(ev['text'], ev['url'])} — <i>{src_txt}</i>")
            else:
                lines.append(f"  📝 {html.escape(ev['text'])} — <i>{src_txt}</i>")

    ai = nar.get("ai", {})
    if ai.get("available"):
        ai_emoji = {
            "organik": "✅", "campuran": "🟡", "terkoordinasi": "🔴", "tidak diketahui": "❔",
        }.get(ai["authenticity"], "")
        lines.append(f"─ 🤖 AI narasi: {ai['authenticity']} {ai_emoji}")
        if ai.get("meme_context"):
            lines.append(f"  🎭 <b>Token ini tentang apa</b>: <i>{html.escape(ai['meme_context'])}</i>")
        lines.append(f"  🧭 <b>Tesis AI</b>: <i>{html.escape(ai['thesis'])}</i>")

    # X (Twitter) tak bisa di-API gratis -> sisipkan link cashtag & community
    # langsung di blok narasi (bukan cuma di baris link bawah) supaya user
    # cek "vibe" manual sebagai bagian dari due diligence narasi, bukan afterthought.
    if "X Cashtag" in links and "X Community" in links:
        lines.append(
            f"─ X (Twitter): {_link('Cashtag $'+sym, links['X Cashtag'])} | "
            f"{_link('Community', links['X Community'])} — cek manual ⚠️"
        )
    lines.append("")
    return lines


def _gmgn_lines(gm: Dict[str, Any]) -> List[str]:
    """
    Blok GMGN OpenAPI (dipakai format_message & format_manual_message --
    identik). INFORMASIONAL SAJA -- security cross-check thd Helius, dev
    holding %, dan tag holder (funding-source tracing asli GMGN, beda dari
    proxy waktu-pembuatan di holders.py). Tak menyentuh skor/hard gate.
    """
    lines: List[str] = []
    gm = gm or {}
    sec = gm.get("security") or {}
    dev = gm.get("dev_holding") or {}
    tags = gm.get("holder_tags") or {}
    top100 = gm.get("top100") or {}
    vol5 = gm.get("volume") or {}

    if vol5.get("available"):
        # Momentum TERKINI (5 menit terakhir) -- ditaruh paling atas krn ini
        # yg jawab keluhan user "notif kerasa telat, udah lewat puncak
        # volume": bandingkan volume 5m vs rata-rata 5-menitan dari volume
        # 1 jam (vol_1h/12) -- kalau vol 5m jauh DI BAWAH rata-rata itu,
        # kemungkinan momentum udah lewat puncak SAAT notif ini dibaca.
        v5 = vol5.get("volume_5m", 0.0)
        v1h = vol5.get("volume_1h", 0.0)
        avg_5m_dari_1h = v1h / 12.0 if v1h > 0 else 0.0
        if avg_5m_dari_1h > 0:
            ratio = v5 / avg_5m_dari_1h
            if ratio >= 1.5:
                mom_emoji, mom_txt = "🚀", "naik drpd rata-rata 1 jam"
            elif ratio <= 0.5:
                mom_emoji, mom_txt = "📉", "turun drpd rata-rata 1 jam -- mgkn sudah lewat puncak"
            else:
                mom_emoji, mom_txt = "➡️", "stabil"
        else:
            mom_emoji, mom_txt = "", ""
        mom_suffix = f" {mom_emoji} <i>({mom_txt})</i>" if mom_txt else ""
        lines.append(
            f"─ GMGN Momentum: 5m ${_h(vol5.get('volume_5m',0))}{mom_suffix} | "
            f"1m ${_h(vol5.get('volume_1m',0))} | 1h ${_h(vol5.get('volume_1h',0))} "
            f"({vol5.get('swaps_5m',0)} swap/5m)"
        )

    if sec.get("available"):
        # honeypot/open_source SERING None (GMGN blm sempat analisis token
        # baru) -- tampilkan "n/a" apa adanya, JANGAN diasumsikan aman.
        hp = sec.get("is_honeypot")
        hp_txt = "🔴 YA" if hp is True else ("✅ tidak" if hp is False else "n/a")
        os_ = sec.get("open_source")
        os_txt = "✅ ya" if os_ is True else ("⚠️ tidak" if os_ is False else "n/a")
        lp_locked = sec.get("lp_locked")
        if lp_locked is True:
            lock_txt = f"✅ {sec.get('lp_lock_pct',0):.0f}%"
        elif lp_locked is False:
            lock_txt = "❌ tidak terkunci"
        else:
            lock_txt = "n/a"
        lines.append(
            f"─ GMGN Security: honeypot {hp_txt} | source terbuka {os_txt} | "
            f"tax {sec.get('buy_tax',0)*100:.0f}%/{sec.get('sell_tax',0)*100:.0f}% | "
            f"LP-lock {lock_txt} <i>(cross-check GMGN)</i>"
        )
    if dev.get("available"):
        status = dev.get("dev_status") or ""
        status_txt = f" ({html.escape(status)})" if status else ""
        lines.append(f"─ GMGN Dev holding: {dev.get('dev_holding_pct',0):.2f}% supply{status_txt}")
    if tags.get("available"):
        lines.append(
            f"─ GMGN wallet tags (dari {tags.get('holder_count',0)} holder): "
            f"smart money {tags.get('smart_money_count',0)} | renowned {tags.get('renowned_count',0)} | "
            f"sniper {tags.get('sniper_count',0)} | rat_trader {tags.get('rat_trader_count',0)} | "
            f"whale {tags.get('whale_count',0)} "
            f"<i>(jumlah wallet, bukan % supply -- funding-source tracing GMGN)</i>"
        )
    if top100.get("available"):
        risk = top100.get("scam_risk_pct", 0)
        flag = "🔴" if risk >= 50 else ("🟡" if risk >= 25 else "✅")
        lines.append(
            f"─ GMGN Top100 pola scam: {flag} risiko tertinggi {risk:.0f}% supply "
            f"<i>(dari {top100.get('sample_count',0)} holder teratas, tag asli funding-source GMGN)</i>"
        )
        # Rincian per kategori -- cuma tampilkan yg > 0% spy notif tak penuh
        # nol semua (mayoritas token wajar tak kena tag2 ini sama sekali).
        breakdown = [
            ("wash-trader", top100.get("wash_trader_pct", 0)),
            ("sandwich-bot", top100.get("sandwich_bot_pct", 0)),
            ("bundler", top100.get("bundler_pct", 0)),
            ("rat_trader", top100.get("rat_trader_pct", 0)),
            ("fresh-wallet", top100.get("fresh_pct", 0)),
            ("wallet baru (is_new)", top100.get("is_new_pct", 0)),
            ("mencurigakan (is_suspicious)", top100.get("is_suspicious_pct", 0)),
        ]
        nonzero = [f"{label} {pct:.0f}%" for label, pct in breakdown if pct > 0]
        if nonzero:
            lines.append(f"  ⚠️ {' | '.join(nonzero)} <i>(% supply top-100)</i>")
    return lines


# ---------------------------------------------------------------------------
# Format pesan ANALISA MANUAL (user kirim CA ke chat bot -- lihat
# sources/telegram_inbound.py & main.py:analyze_by_mint)
# ---------------------------------------------------------------------------
def format_manual_message(ctx: Dict[str, Any]) -> str:
    """
    BEDA dari format_message(): dipakai utk hasil analisa ON-DEMAND (mint apa
    pun yang user kirim manual), BUKAN hasil auto-screening yang sudah lolos
    hard gate. Karena itu hard gate ditampilkan APA ADANYA (pass/fail
    sungguhan dari stage2_pass/stage3_pass/holders, bukan diasumsikan lolos
    spt format_message), dan pool Meteora OPSIONAL (bisa None kalau token tak
    nge-LP di Meteora -- bagian Kualitas LP ditandai n/a, bukan gagal total).
    Selalu dikirim balik ke user terlepas dari verdict/skor -- ini permintaan
    eksplisit, bukan notifikasi auto yang perlu di-filter anti-spam.
    """
    sym = html.escape(ctx["symbol"])
    m = ctx["metrics"]
    pool = ctx.get("pool_data")
    sec = ctx["security"]
    h = ctx["holders"]
    lp = ctx.get("lp") or {}
    vol = ctx["vol"]
    vwap = ctx.get("vwap", {})
    lc = ctx.get("lunarcrush", {})
    jup = ctx.get("jupiter", {})
    nar = ctx["narrative"]
    links = ctx["links"]
    warns: List[str] = ctx.get("warnings", [])
    stage2_pass = ctx.get("stage2_pass", True)
    stage2_reasons = ctx.get("stage2_reasons", [])
    stage3_pass = ctx.get("stage3_pass", True)
    stage3_reasons = ctx.get("stage3_reasons", [])

    lines: List[str] = []
    lines.append(
        f"🔍 <b>HASIL ANALISA MANUAL — ${sym}</b>  "
        f"<i>(skor {ctx['score']:.0f}/100, verdict internal {ctx['verdict']})</i>"
    )
    lines.append(
        "<i>Hard gate ditampilkan apa adanya (bisa gagal) -- ini bukan hasil "
        "auto-screening yang sudah difilter, jadi baca ⚠️/❌ dgn cermat.</i>"
    )
    if pool:
        lines.append(f"Pool: {_link(pool.get('name') or 'Meteora', links.get('Meteora',''))}")
    else:
        lines.append("Pool Meteora: tak ditemukan (token mungkin tak nge-LP di Meteora DLMM)")
    lines.append("")

    lines.append("📊 <b>HARD GATES</b>")
    lines.append(
        f"─ MCap ${_h(m.get('market_cap', 0))} | Vol24h ${_h(m.get('volume_h24', 0))} {_yn(stage2_pass)}"
    )
    if not stage2_pass and stage2_reasons:
        # html.escape WAJIB -- reasons berisi "<"/">" literal (mis. "vol24h
        # $X < $Y"), tanpa escape Telegram parse_mode HTML menolak SELURUH
        # pesan (400 "Unsupported start tag") krn dikira tag rusak.
        lines.append(f"  ⚠️ {html.escape('; '.join(stage2_reasons))}")
    if pool:
        lines.append(
            f"─ TVL ${_h(pool.get('tvl_usd', 0))} | Bin {pool.get('bin_step','?')} | "
            f"Base {pool.get('base_fee_pct','?')}% | Quote {pool.get('_quote_symbol','?')}"
        )
        lines.append(f"─ Global fee {pool.get('_cum_fee_sol', 0)} SOL")
    else:
        lines.append("─ TVL / Bin / Fee pool: n/a (bukan pool Meteora)")
    tax_pct = (sec.get("transfer_fee_bps", 0) or 0) / 100.0
    lines.append(
        f"─ no-mint {_yn(sec.get('mint_authority') is None)} "
        f"no-freeze {_yn(sec.get('freeze_authority') is None)} "
        f"no-tax {_yn(tax_pct <= config.MAX_TRANSFER_FEE_BPS/100)} {_yn(stage3_pass)}"
    )
    if not stage3_pass and stage3_reasons:
        lines.append(f"  ⚠️ {html.escape('; '.join(stage3_reasons))}")
    if h.get("available"):
        lines.append(f"─ Top10: {h['top10_pct']}% {_yn(h['top10_gate_pass'])}")
        coord = h.get("coordination_label", "n/a")
        coord_emoji = {"TINGGI": "🔴", "SEDANG": "🟡", "WAJAR": "✅"}.get(coord, "")
        lines.append(f"─ Indikasi coordinated trading: {coord_emoji} {coord}")
        cluster_n = h.get("largest_cluster_wallets", 0)
        if cluster_n >= 2:
            lines.append(
                f"─ Cluster terbesar: {h.get('largest_cluster_pct',0)}% supply / {cluster_n} wallet "
                f"{_yn(h.get('cluster_gate_pass', True))}"
            )
    else:
        lines.append("─ Holder: data tak tersedia ⚠️")
    if jup.get("available"):
        label = jup.get("organic_label") or "?"
        jup_emoji = {"high": "✅", "medium": "🟡", "low": "🔴"}.get(label, "")
        lines.append(
            f"─ Jupiter Organic Score: {jup.get('organic_score',0):.0f}/100 ({label}) {jup_emoji}"
        )
    lines.extend(_gmgn_lines(ctx.get("gmgn", {})))
    lines.append("")

    if pool:
        lines.append("💰 <b>KUALITAS LP</b>")
        fee_flag = "✅" if lp.get("fee_tvl_daily_pct", 0) >= config.FEE_TVL_DAILY_GOOD_PCT else "⚠️"
        vol_flag = "✅" if lp.get("vol_tvl", 0) >= config.VOL_TVL_GOOD_RATIO else "⚠️"
        est = " (est)" if lp.get("fee_estimated") else ""
        lines.append(
            f"─ Fee/TVL harian: {lp.get('fee_tvl_daily_pct',0)}%{est} {fee_flag} | "
            f"Vol/TVL: {lp.get('vol_tvl',0)}× {vol_flag}"
        )
        if lp.get("pool_age_hours") is not None:
            lines.append(f"─ Umur pool: {lp['pool_age_hours']:.0f} jam")
        lines.append("")

    lines.append(
        f"📉 <b>Volatilitas</b>: {vol['note']} "
        f"{'✅' if not vol['vertical_death'] else '🔴 mati vertikal'}"
    )
    if vwap.get("available"):
        pct = vwap.get("ratio_pct", 0.0)
        pos = "di atas" if vwap.get("above_vwap") else "di bawah"
        lines.append(f"─ VWAP (sejak pool dibuat): harga {pos} VWAP {abs(pct):.0f}%")
    lines.append("")

    lines.extend(_narrative_lines(nar, lc, links, sym))

    if warns:
        lines.append("⚠️ <b>CATATAN:</b> " + "; ".join(html.escape(x) for x in warns[:4]))
        lines.append("")

    lines.append("🔗 <b>VERIFIKASI MANUAL</b> (klik):")
    order = [
        "GMGN", "Bubblemaps", "DevsNightmare", "Deepnets", "RugCheck", "SolScan",
        "pump.fun", "X search", "TikTok", "Instagram",
    ]
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
