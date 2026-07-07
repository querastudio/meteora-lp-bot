"""
test_notify.py — Kirim CONTOH notifikasi Telegram dgn data sintetis (bukan real).

Tujuan: user bisa lihat tampilan format notifikasi terbaru (cluster/bundle
detection, coordinated trading, narasi Viralitas+Daya Tahan+Reddit+evidence,
link X, dst.) tanpa perlu menunggu token asli lolos semua gate. TIDAK
menyentuh state_data.json, TIDAK menjalankan pipeline nyata.
"""

import logging

import notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("test_notify")


def build_sample_ctx() -> dict:
    symbol = "PEPEC"
    mint = "PePeCMintExampleXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    pool_addr = "PoolExampleXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

    pool = {
        "address": pool_addr, "name": f"{symbol}-SOL",
        "tvl_usd": 18500, "bin_step": 100, "base_fee_pct": 2.0,
        "_cum_fee_sol": 32.4, "_quote_symbol": "SOL",
    }
    metrics = {
        "market_cap": 610000, "volume_h24": 2100000,
        "symbol": symbol, "price_usd": 0.00812,
    }
    sec = {"mint_authority": None, "freeze_authority": None, "transfer_fee_bps": 0}
    hold = {
        "available": True, "top10_pct": 21.4, "top10_gate_pass": True,
        "inspected_count": 20, "fresh_pct": 15.0, "empty_pct": 10.0, "young_pct": 20.0,
        "coordination_label": "WAJAR",
        "largest_cluster_pct": 12.5, "largest_cluster_wallets": 3,
        "cluster_gate_pass": True,
    }
    lp = {
        "fee_tvl_daily_pct": 6.8, "vol_tvl": 3.2, "lp_conc_score": 0.82,
        "pool_age_hours": 96, "fee_estimated": False,
    }
    vol = {"note": "turun bertahap, volume tahan 4 hari", "vertical_death": False}
    vwap = {
        "available": True, "vwap": 0.00623, "ratio_pct": 30.3,
        "above_vwap": True, "momentum_score": 0.88, "candle_count": 96,
    }
    lunarcrush = {
        "available": True, "galaxy_score": 72.0, "sentiment_pct": 81.0,
        "num_contributors": 340, "num_posts": 512, "interactions_24h": 89000,
        "social_score": 0.72,
    }
    jupiter_organic = {
        "available": True, "organic_score": 78.0, "organic_label": "high",
        "organic_signal_score": 0.78,
    }
    gmgn_data = {
        "security": {
            "available": True, "is_honeypot": False, "open_source": True,
            "buy_tax": 0.0, "sell_tax": 0.0, "lp_locked": True, "lp_lock_pct": 95.0,
            "flags": [],
        },
        "dev_holding": {"available": True, "dev_holding_pct": 3.2, "dev_status": "creator_close"},
        "holder_tags": {
            "available": True, "smart_money_count": 58, "renowned_count": 18,
            "sniper_count": 45, "rat_trader_count": 1, "whale_count": 7, "holder_count": 5928,
        },
        "top100": {
            "available": True, "scam_risk_pct": 8.0,
            "fresh_pct": 8.0, "fresh_count": 4,
            "wash_trader_pct": 0.0, "wash_trader_count": 0,
            "sandwich_bot_pct": 0.0, "sandwich_bot_count": 0,
            "bundler_pct": 0.0, "bundler_count": 0,
            "rat_trader_pct": 0.0, "rat_trader_count": 0,
            "is_new_pct": 6.0, "is_new_count": 3,
            "is_suspicious_pct": 2.0, "is_suspicious_count": 1,
            "sample_count": 100, "coverage_pct": 78.6,
        },
    }
    narrative = {
        "category": "animal", "keyword": symbol, "score": 0.88,
        "viral_label": "🔥 SANGAT VIRAL", "durability_label": "TAHAN LAMA",
        "breadth_score": 1.0, "volume_score": 0.82, "diversity_score": 0.9,
        "durability_score": 0.95,
        "insights": [
            "aktif di 5/5 platform (bukan 1 sumber saja)",
            "8 subreddit & 11 channel berbeda ikut bahas -- indikasi organik lintas komunitas",
        ],
        "evidence": [
            {
                "text": f"{symbol} community growing fast, new memes every day",
                "source": "Reddit r/CryptoMoonShots (1800 upvote)",
                "url": "https://reddit.com/r/example/1",
            },
            {
                "text": f"KOL bullposting {symbol} sebagai narasi memecoin cycle baru",
                "source": "News: ExampleCrypto",
                "url": "https://example.com/news/1",
            },
        ],
        "trends": {"available": True, "rising": True, "sustained": True, "avg": 68.0},
        "youtube": {"available": True, "video_count": 22, "total_views": 1250000, "channel_count": 11},
        "reddit": {
            "available": True, "post_count": 35, "total_score": 7200,
            "total_comments": 980, "subreddit_count": 8, "posts_last24h": 6,
        },
        "news": {"available": True, "article_count": 12, "domain_count": 6},
        "pumpfun": {
            "available": True, "member_count": 420, "post_count": 88,
            "total_likes": 310, "total_replies": 140, "distinct_posters": 65,
            "spam_count": 2, "avg_follower_count": 340.5, "posts_last24h": 14,
            "top_posts": [{"text": f"{symbol} to the moon lfg", "username": "degen123", "likeCount": 42}],
        },
        "ai": {
            "available": True, "authenticity": "organik",
            "meme_context": (
                f"${symbol} mengangkat tema hewan lucu viral ala Pepe -- komunitasnya di chat pump.fun "
                "ramai bikin varian meme baru tiap hari, momentumnya ikut gelombang narasi 'animal coin' terbaru."
            ),
            "thesis": (
                "Fee/TVL sehat dan volume tahan bareng distribusi holder wajar -- kombinasi ini "
                "cocok utk LP pasif jangka menengah, narasi komunitas juga tampak organik lintas platform."
            ),
            "score_multiplier": 1.0,
        },
    }
    warnings = ["LP-lock belum terverifikasi otomatis — cek manual"]

    links = notify.build_manual_links(mint, pool_addr, symbol)

    return {
        "verdict": "STRONG",
        "score": 91,
        "symbol": symbol,
        "mint": mint,
        "metrics": metrics,
        "pool_data": pool,
        "security": sec,
        "holders": hold,
        "lp": lp,
        "vol": vol,
        "vwap": vwap,
        "lunarcrush": lunarcrush,
        "jupiter": jupiter_organic,
        "gmgn": gmgn_data,
        "narrative": narrative,
        "warnings": warnings,
        "links": links,
    }


def main() -> None:
    ctx = build_sample_ctx()
    text = "🧪 <b>INI CONTOH/TEST</b> — bukan sinyal beli sungguhan\n\n" + notify.format_message(ctx)
    ok = notify.send(text)
    if ok:
        log.info("Test notifikasi terkirim.")
    else:
        log.error("Gagal kirim test notifikasi -- cek TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID.")


if __name__ == "__main__":
    main()
