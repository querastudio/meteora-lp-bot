"""
main.py — Orkestrasi pipeline cascade (Stage 1 -> 7).

Alur per run (dipanggil cron GitHub Actions tiap 5 menit):
  0. Cek pesan Telegram baru -- fitur "kirim CA, bot balas analisa" on-demand
     (lihat sources/telegram_inbound.py & analyze_by_mint di bawah). Delay
     maks ~5 menit (polling di cron yang sama, bukan webhook -- no infra baru).
  1. Load state (anti-duplikat + riwayat harga utk Stage 6) & harga SOL.
  2. Fetch pool Meteora, urut aktivitas.
  3. Cascade: Stage 1 (pool) -> Stage 2 (mcap/volume) -> Stage 3 (keamanan)
     -> Stage 4 (holder) -> Stage 5 (LP) -> Stage 6 (volatilitas) -> Stage 7 (narasi).
     Gugur di stage awal = tak lanjut ke stage mahal (hemat rate limit).
  4. Scoring -> verdict. Kirim Telegram (anti-duplikat). Simpan state.

Prinsip: cepat, idempoten, degrade gracefully. Satu API mati != run crash.
"""

import logging
import sys
from typing import Any, Dict, List, Optional

import config
import notify
import scoring
import state as state_mod
from screening import hard_filters, holders, lp_quality, volatility
from sources import (
    dexscreener, geckoterminal, gemini, gmgn, groq, helius, jupiter, lunarcrush,
    meteora, narrative, telegram_inbound,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def run() -> int:
    log.info("=== Meteora LP screening run mulai ===")
    # Circuit breaker LunarCrush per-run (lihat sources/lunarcrush.py) --
    # rate limit daily/minute-nya tak reset di tengah run, tp BISA reset run
    # berikutnya, jadi wajib di-reset tiap run baru mulai.
    lunarcrush.reset_run()
    st = state_mod.load()
    sol_price = dexscreener.get_sol_price_usd()
    log.info("Harga SOL: $%.2f", sol_price)

    # ---- Fitur "kirim CA, bot balas analisa" (on-demand, mint apa pun) ----
    offset = state_mod.get_telegram_offset(st)
    requested_mints, next_offset = telegram_inbound.poll_new_mints(offset)
    state_mod.set_telegram_offset(st, next_offset)
    for req_mint in dict.fromkeys(requested_mints):  # dedup, jaga urutan
        try:
            analyze_by_mint(req_mint, st, sol_price)
        except Exception as e:  # noqa: BLE001 — 1 permintaan error != crash run
            log.exception("Error analisa manual mint %s: %s", req_mint, e)

    pools = meteora.fetch_pools(config.MAX_POOLS_PER_RUN)
    if not pools:
        log.warning("Tak ada pool dari Meteora, selesai.")
        return 0

    # ---- STAGE 1: hard filter pool (murah, 0 call) ----
    stage1_pass: List[Dict[str, Any]] = []
    for pool in pools:
        ok, quote_sym, reasons = hard_filters.stage1_pool(pool, sol_price)
        if not ok:
            log.debug("S1 gugur %s: %s", pool.get("name"), reasons)
            continue
        stage1_pass.append(pool)
    log.info("Stage 1: %d/%d pool lolos", len(stage1_pass), len(pools))

    # Batasi kandidat mahal per run (hemat Helius rate limit & waktu cron).
    candidates = stage1_pass[: config.MAX_EXPENSIVE_CANDIDATES]

    sent = 0
    for pool in candidates:
        try:
            result = _process_candidate(pool, st, sol_price)
            if result:
                sent += 1
        except Exception as e:  # noqa: BLE001 — 1 token error != crash run
            log.exception("Error proses pool %s: %s", pool.get("name"), e)

    # Simpan state (riwayat harga + notified) untuk run berikutnya.
    state_mod.save(st)
    log.info("=== Selesai. %d notifikasi terkirim. ===", sent)
    return sent


def _process_candidate(pool: Dict[str, Any], st: Dict[str, Any], sol_price: float):
    """Jalankan Stage 2-7 untuk satu pool kandidat. Return True bila dikirim."""
    mint = hard_filters.base_mint_of(pool)
    name = pool.get("name", "?")

    # ---- STAGE 2: token metrics (mcap/volume) ----
    metrics = dexscreener.get_token_metrics(mint)
    if not metrics:
        log.info("S2 skip %s: metrik token tak tersedia", name)
        return False

    symbol = metrics["symbol"]
    # Catat harga ke riwayat (dipakai Stage 6 utk estimasi volume-tahan-lama)
    # & deteksi ATH BARU (harga menembus rekor tertinggi tercatat -- sinyal
    # momentum genuine breakout, beda dari sekadar "naik dari kemarin").
    is_new_ath = state_mod.record_price(st, mint, metrics["price_usd"], symbol)

    ok2, reasons2 = hard_filters.stage2_token(metrics)
    if not ok2:
        log.info("S2 gugur $%s: %s", symbol, reasons2)
        return False

    # ---- STAGE 2.5: volume ORGANIK & TINGGI (murah, TANPA Helius) ----
    # Sengaja ditaruh SEDINI mungkin sblm Stage 3/4 yg mahal & rawan rate
    # limit (Helius) -- kandidat yg volume-nya tak sepadan mcap-nya digugurkan
    # DULU di sini, hemat waktu & rate-limit Helius utk kandidat lain yg lebih
    # layak, sekaligus fast-track token "runner" asli ke notifikasi (permintaan
    # user: signal secepat mungkin begitu ada token runner volume tinggi).
    jup = jupiter.organic_score(mint)
    gm_volume = gmgn.volume_momentum(mint)
    vol_organic = hard_filters.stage2_volume_organic(metrics["market_cap"], pool.get("_cum_fee_sol", 0.0))
    if config.VOLUME_ORGANIC_HARD_GATE and not vol_organic["pass"]:
        log.info("S2.5 gugur $%s: %s", symbol, vol_organic["reason"])
        return False

    # ---- STAGE 3: keamanan kontrak (Helius) — paling kritis ----
    sec = helius.get_security_info(mint)
    ok3, hard3, warn3 = hard_filters.stage3_security(sec)
    if not ok3:
        log.info("S3 gugur $%s (SKIP keamanan): %s", symbol, hard3)
        if config.SEND_SKIP_AUDIT:
            _maybe_send_skip(mint, pool, symbol, hard3, st)
        return False

    warnings: List[str] = list(warn3)

    # ---- STAGE 4: distribusi holder (hard gate top10 + cluster + soft heuristik) ----
    hold = holders.analyze(mint, sol_price)
    if hold.get("available") and not hold["top10_gate_pass"]:
        log.info("S4 gugur $%s: top10 %.1f%% >= %.0f%%", symbol, hold["top10_pct"], config.MAX_TOP10_SUPPLY_PCT)
        return False
    if hold.get("available") and not hold["cluster_gate_pass"]:
        log.info(
            "S4 gugur $%s: cluster terbesar %.1f%% >= %.0f%% (%d wallet dibuat berdekatan -- kemungkinan 1 bundler kuasai mayoritas)",
            symbol, hold["largest_cluster_pct"], config.MAX_CLUSTER_SUPPLY_PCT, hold["largest_cluster_wallets"],
        )
        return False
    if hold.get("available") and hold.get("coordination_label") == "TINGGI":
        log.info(
            "S4 gugur $%s: indikasi KUAT coordinated trading (%.0f%% fresh, %.0f%% saldo rendah, %.0f%% umur muda di top%d)",
            symbol, hold.get("fresh_pct", 0), hold.get("empty_pct", 0), hold.get("young_pct", 0), hold.get("inspected_count", 0),
        )
        return False
    if not hold.get("available"):
        warnings.append("distribusi holder tak terverifikasi")
    if hold.get("note"):
        warnings.append(hold["note"])

    # ---- STAGE 5: kualitas LP ----
    lp = lp_quality.analyze(pool, metrics)

    # ---- STAGE 6: volatilitas turun-stabil (hard SKIP bila mati vertikal) ----
    history = state_mod.get_price_history(st, mint)
    vol = volatility.analyze(metrics, history)
    if vol["vertical_death"]:
        log.info("S6 gugur $%s: mati vertikal (ATH palsu / pump-dump)", symbol)
        return False

    # ---- STAGE 7: narasi viral (degrade gracefully) ----
    nar = narrative.evaluate_narrative(metrics.get("name", ""), symbol, mint)

    # ---- MOMENTUM VWAP (opsional, soft score -- degrade gracefully) ----
    # Pakai pool address yg SAMA dgn sumber harga "sekarang" (best-pair
    # Dexscreener), bukan selalu pool Meteora -- token bisa trading di >1
    # DEX dgn riwayat harga beda (pelajaran dari bug ATH $manlet dulu).
    vwap = {}
    if config.VWAP_MOMENTUM_ENABLED:
        vwap_pool_address = metrics.get("pair_address") or pool["address"]
        vwap = geckoterminal.vwap_signal(vwap_pool_address, metrics["price_usd"])

    # ---- LUNARCRUSH (opsional, BERBAYAR -- Galaxy Score/sentiment X) ----
    # Nyaris selalu n/a utk token super baru (belum ter-index LunarCrush),
    # itu wajar -- degrade gracefully, lihat sources/lunarcrush.py.
    lc = lunarcrush.social_signal(symbol)

    # ---- GMGN (gratis) -- security cross-check, dev holding %, tag holder ----
    # (volume_momentum sudah dipanggil di Stage 2.5 di atas -- jup & gm_volume
    # dipakai lagi di sini, bukan dipanggil ulang.)
    # INFORMASIONAL SAJA (tampil di HARD GATES section notif) -- tak menyentuh
    # skor/hard gate, hard gate keamanan/holder tetap otoritatif dari Helius.
    # Lihat sources/gmgn.py.
    gm_sec = gmgn.token_security(mint)
    gm_dev = gmgn.dev_holding(mint)
    gm_holders = gmgn.top_holder_tags(mint)
    gm_top100 = gmgn.top100_cluster_analysis(mint)
    warnings.extend(gm_sec.get("flags", []))

    # ---- Sintesis AI (opsional -- lihat sources/gemini.py & ai_common.py) ----
    # authenticity: nudge nar['score'] dlm rentang 0.6-1.0x dari kutipan
    # Reddit/News SAJA, TIDAK pernah menyentuh hard gate.
    # thesis: sintesis SEMUA metrik (LP/volatilitas/holder/narasi/VWAP/
    # Jupiter) jadi 1-2 kalimat gambaran besar -- PURE TEKS, tak pengaruhi
    # skor apa pun (biar AI tak cuma nilai gaya PR media spt kasus $PUMPCADE,
    # tapi kasih verdict holistik yg mencakup volume/komunitas/distribusi
    # suplai juga -- lihat sources/ai_common.py).
    # Coba Gemini dulu; kalau gagal/limit, fallback ke Groq (rate limit
    # gratisnya lebih longgar). Degrade gracefully kalau keduanya gagal/key
    # kosong -- skor narasi tetap rule-based biasa, tak ada thesis.
    #
    # Gate bukti minimum: kalau Reddit & News keduanya terlalu tipis, JANGAN
    # panggil AI sama sekali -- lebih baik "tak menilai" drpd menilai keliru
    # dari data hampir kosong (lihat config.AI_MIN_REDDIT_POSTS/NEWS_ARTICLES).
    # Thesis butuh bukti kualitatif nyata soal narasi juga, bukan cuma angka
    # metrik lain -- jadi gate yang sama berlaku utk thesis, bukan cuma
    # authenticity.
    reddit_cnt = nar.get("reddit", {}).get("post_count", 0)
    news_cnt = nar.get("news", {}).get("article_count", 0)
    pumpfun_cnt = nar.get("pumpfun", {}).get("post_count", 0)
    has_enough_evidence = (
        reddit_cnt >= config.AI_MIN_REDDIT_POSTS
        or news_cnt >= config.AI_MIN_NEWS_ARTICLES
        or pumpfun_cnt >= config.AI_MIN_PUMPFUN_POSTS
    )

    nar_ai = {}
    if has_enough_evidence:
        nar_ai = gemini.assess_narrative(
            symbol, nar.get("category", "unknown"), nar, lp, vol, hold, vwap, jup, vol_organic, is_new_ath,
        )
        if not nar_ai.get("available"):
            nar_ai = groq.assess_narrative(
                symbol, nar.get("category", "unknown"), nar, lp, vol, hold, vwap, jup, vol_organic, is_new_ath,
            )
    else:
        log.info(
            "$%s: bukti narasi terlalu tipis (reddit=%d, news=%d, pumpfun=%d) -- skip AI check",
            symbol, reddit_cnt, news_cnt, pumpfun_cnt,
        )
    if nar_ai.get("available"):
        nar["score"] = round(nar.get("score", 0.0) * nar_ai["score_multiplier"], 3)
    nar["ai"] = nar_ai

    # ---- SCORING & VERDICT ----
    scored = scoring.compute(lp, vol, hold, nar, warnings, vwap, lc, jup, vol_organic, is_new_ath)
    verdict = scored["verdict"]
    log.info("$%s -> %s (skor %.0f) breakdown=%s", symbol, verdict, scored["score"], scored["breakdown"])

    if verdict == "SKIP":
        return False  # hard gate lolos tapi skor terlalu rendah -> tak menarik

    # Anti-duplikat.
    if not state_mod.should_notify(st, mint, verdict):
        log.info("$%s: sudah dinotif (%s), skip re-notif", symbol, verdict)
        return False

    links = notify.build_manual_links(mint, pool["address"], symbol)
    ctx = {
        "verdict": verdict,
        "score": scored["score"],
        "symbol": symbol,
        "mint": mint,
        "metrics": metrics,
        "pool_data": pool,
        "security": sec,
        "holders": hold,
        "lp": lp,
        "vol": vol,
        "vwap": vwap,
        "lunarcrush": lc,
        "jupiter": jup,
        "gmgn": {"security": gm_sec, "dev_holding": gm_dev, "holder_tags": gm_holders, "top100": gm_top100, "volume": gm_volume},
        "narrative": nar,
        "warnings": warnings,
        "links": links,
        "vol_organic": vol_organic,
        "is_new_ath": is_new_ath,
    }
    text = notify.format_message(ctx)
    if notify.send(text):
        state_mod.mark_notified(st, mint, verdict)
        return True
    return False


def analyze_by_mint(mint: str, st: Dict[str, Any], sol_price: float) -> bool:
    """
    Analisa 1 token by mint address atas permintaan MANUAL user (kirim CA ke
    chat bot -- lihat sources/telegram_inbound.py). BEDA dari
    _process_candidate:
      - TIDAK di-gate hard filter -- user sengaja minta lihat token spesifik
        ini, jadi hasil tetap dikirim walau ada gate yang gagal (ditampilkan
        apa adanya oleh notify.format_manual_message, bukan disembunyikan).
      - TIDAK kena anti-duplikat should_notify -- selalu dibalas tiap diminta.
      - Pool Meteora OPSIONAL (dicari via meteora.fetch_pool_by_mint) --
        token yang tak nge-LP di Meteora tetap dianalisa (keamanan/holder/
        narasi), cuma bagian Kualitas LP yang n/a.
    """
    log.info("Analisa manual diminta utk mint %s...", mint[:8])
    metrics = dexscreener.get_token_metrics(mint)
    if not metrics:
        notify.send(
            f"🔍 Analisa manual: mint <code>{mint}</code> tak ditemukan di "
            f"Dexscreener (bukan token SPL aktif trading, atau alamat salah)."
        )
        return False

    symbol = metrics["symbol"]
    is_new_ath = state_mod.record_price(st, mint, metrics["price_usd"], symbol)

    stage2_pass, stage2_reasons = hard_filters.stage2_token(metrics)

    sec = helius.get_security_info(mint)
    stage3_pass, stage3_reasons, warn3 = hard_filters.stage3_security(sec)
    warnings: List[str] = list(warn3)

    hold = holders.analyze(mint, sol_price)
    if not hold.get("available"):
        warnings.append("distribusi holder tak terverifikasi")
    if hold.get("note"):
        warnings.append(hold["note"])

    pool: Optional[Dict[str, Any]] = meteora.fetch_pool_by_mint(mint)
    if pool:
        lp = lp_quality.analyze(pool, metrics)
    else:
        lp = {
            "fee_tvl_daily_pct": 0.0, "fee_estimated": False, "vol_tvl": 0.0,
            "pool_age_hours": None, "fee_score": 0.5, "vol_score": 0.5,
            "age_score": 0.5, "lp_conc_score": 0.5, "lp_conc_estimated": True,
        }
        warnings.append("bukan pool Meteora / pool tak ditemukan -- kualitas LP n/a")

    history = state_mod.get_price_history(st, mint)
    vol = volatility.analyze(metrics, history)

    nar = narrative.evaluate_narrative(metrics.get("name", ""), symbol, mint)

    vwap: Dict[str, Any] = {}
    if config.VWAP_MOMENTUM_ENABLED:
        vwap_pool_address = metrics.get("pair_address") or (pool["address"] if pool else "")
        if vwap_pool_address:
            vwap = geckoterminal.vwap_signal(vwap_pool_address, metrics["price_usd"])

    lc = lunarcrush.social_signal(symbol)
    jup = jupiter.organic_score(mint)
    gm_volume = gmgn.volume_momentum(mint)
    # Analisa manual TAK pernah lewat stage1_pool() (pool ditemukan belakangan,
    # opsional) -- hitung cum_fee_sol inline drpd pool["_cum_fee_sol"], DAN
    # tak pernah dijadikan hard gate di sini (filosofi analyze_by_mint: user
    # sengaja minta lihat token INI, tampilkan apa adanya spt gate lain).
    cum_fee_sol = (pool.get("cumulative_fee_usd", 0) / sol_price) if pool and sol_price > 0 else 0.0
    vol_organic = hard_filters.stage2_volume_organic(metrics["market_cap"], cum_fee_sol)

    gm_sec = gmgn.token_security(mint)
    gm_dev = gmgn.dev_holding(mint)
    gm_holders = gmgn.top_holder_tags(mint)
    gm_top100 = gmgn.top100_cluster_analysis(mint)
    warnings.extend(gm_sec.get("flags", []))

    reddit_cnt = nar.get("reddit", {}).get("post_count", 0)
    news_cnt = nar.get("news", {}).get("article_count", 0)
    pumpfun_cnt = nar.get("pumpfun", {}).get("post_count", 0)
    has_enough_evidence = (
        reddit_cnt >= config.AI_MIN_REDDIT_POSTS
        or news_cnt >= config.AI_MIN_NEWS_ARTICLES
        or pumpfun_cnt >= config.AI_MIN_PUMPFUN_POSTS
    )
    nar_ai = {}
    if has_enough_evidence:
        nar_ai = gemini.assess_narrative(
            symbol, nar.get("category", "unknown"), nar, lp, vol, hold, vwap, jup, vol_organic, is_new_ath,
        )
        if not nar_ai.get("available"):
            nar_ai = groq.assess_narrative(
                symbol, nar.get("category", "unknown"), nar, lp, vol, hold, vwap, jup, vol_organic, is_new_ath,
            )
    else:
        log.info(
            "$%s (manual): bukti narasi terlalu tipis (reddit=%d, news=%d, pumpfun=%d) -- skip AI check",
            symbol, reddit_cnt, news_cnt, pumpfun_cnt,
        )
    if nar_ai.get("available"):
        nar["score"] = round(nar.get("score", 0.0) * nar_ai["score_multiplier"], 3)
    nar["ai"] = nar_ai

    scored = scoring.compute(lp, vol, hold, nar, warnings, vwap, lc, jup, vol_organic, is_new_ath)
    log.info(
        "$%s (manual) -> skor %.0f (verdict internal %s) breakdown=%s",
        symbol, scored["score"], scored["verdict"], scored["breakdown"],
    )

    links = notify.build_manual_links(mint, pool["address"] if pool else "", symbol)
    ctx = {
        "verdict": scored["verdict"],
        "score": scored["score"],
        "symbol": symbol,
        "mint": mint,
        "metrics": metrics,
        "pool_data": pool,
        "security": sec or {},
        "holders": hold,
        "lp": lp,
        "vol": vol,
        "vwap": vwap,
        "lunarcrush": lc,
        "jupiter": jup,
        "gmgn": {"security": gm_sec, "dev_holding": gm_dev, "holder_tags": gm_holders, "top100": gm_top100, "volume": gm_volume},
        "narrative": nar,
        "warnings": warnings,
        "links": links,
        "stage2_pass": stage2_pass,
        "stage2_reasons": stage2_reasons,
        "stage3_pass": stage3_pass,
        "stage3_reasons": stage3_reasons,
        "vol_organic": vol_organic,
        "is_new_ath": is_new_ath,
    }
    text = notify.format_manual_message(ctx)
    return notify.send(text)


def _maybe_send_skip(mint, pool, symbol, reasons, st):
    """Kirim SKIP ringkas untuk audit (opsional, default off)."""
    if not state_mod.should_notify(st, mint, "SKIP"):
        return
    txt = f"🔴 <b>SKIP — ${symbol}</b>\n{pool.get('name','')}\nAlasan: " + "; ".join(reasons)
    if notify.send(txt):
        state_mod.mark_notified(st, mint, "SKIP")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001
        log.exception("Run gagal total: %s", e)
        sys.exit(1)
