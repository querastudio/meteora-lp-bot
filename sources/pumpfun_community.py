"""
sources/pumpfun_community.py — Chat komunitas pump.fun (backend "Coin
Communities", coin-communities.xyz) -- KANAL NARASI ke-5, sejajar dgn
Reddit/YouTube/News di narrative.py.

DIKONFIRMASI USER (dilihat langsung dari network request pump.fun): ini
backend ASLI fitur "Community"/chat di halaman token pump.fun -- setiap
token pump.fun otomatis punya community di sini begitu token dibuat,
coverage jauh lebih tinggi drpd Reddit/YouTube utk token yg BARU saja
migrasi ke Meteora (biasanya blm sempat viral di Reddit/YouTube/News,
tapi chat komunitas pump.fun-nya sendiri sudah aktif sejak token dibuat).

Keunggulan dibanding Reddit/YouTube/News: di-key oleh token_address
on-chain (mint), BUKAN text search berbasis simbol -- jadi TAK butuh
filter relevansi ticker-collision (lihat _looks_crypto_related di
narrative.py) krn mint address unik per token, tak mungkin nyasar ke
token/topik lain.

Auth -- DUA kredensial BEDA, dikonfirmasi live (7 Juli 2026):
  - x-api-key (PUMPFUN_COMMUNITY_API_KEY) -- dari menu "API keys" dashboard,
    HANYA dipakai utk getCommunity() (cek community ada/tidak). Apply via
    coincommunities.org -- registrasi akun bisnis (email+password) +
    verifikasi email, TIDAK sesimpel apply GMGN.
  - x-server-key + x-server-secret (PUMPFUN_COMMUNITY_SERVER_KEY/_SECRET)
    -- dari menu "Server API keys" (TERPISAH dari "API keys" biasa!),
    dipakai utk baca pesan & member (endpoint */server, didesain khusus
    backend/bot tanpa sesi login user). Live run konfirmasi: getMessages/
    getCommunityMembers pakai x-api-key BALIK 401 walau key sama persis
    yg sukses di getCommunity() -- endpoint itu ternyata butuh kredensial
    server, meski docs SDK sebut "api key" auth (docs vs realita beda lg,
    pola sama spt GMGN sebelumnya).
Kosongkan salah satu/semua utk skip bagian terkait -- narasi tetap jalan
dari kanal lain (degrade gracefully, JANGAN crash run).

Skema respons: getCommunity() dikonfirmasi resmi dari contoh docs SDK
(wrapper { community: {...} }) DAN dari live run (field asli persis
{id, tokenAddress, createdAt}, TANPA memberCount -- makanya member_count
fallback ke len(member_rows)). messages/members endpoint (skema */server)
parsing DEFENSIF (coba beberapa nama key wrapper umum) + log struktur
mentah sekali per token -- field asli blm terverifikasi krn baru 401
(scope server key blm diverifikasi user saat modul ini ditulis).
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config
from sources import http

log = logging.getLogger("pumpfun_community")

BASE = "https://api.coin-communities.xyz"

# Panjang karakter minimum spy pesan dianggap "opini/tesis substantif"
# (bukan reaksi refleks pendek spt "gm"/"lfg"/"Tung Tung Tung?") -- proxy
# kasar, bukan NLP asli, tapi cukup memisahkan reaksi 1-baris dari member
# yg beneran nulis pendapat soal coin-nya.
_OPINION_MIN_LENGTH = 60

# Frasa umum bot-spam yang disebar SAMA PERSIS ke banyak komunitas token
# berbeda (bukan cuma token ini) -- kasus nyata: pesan panjang "Stock
# Upgrade Now Live! ... convert meme coin holdings into stock-backed
# positions ... staking rewards" lolos filter _OPINION_MIN_LENGTH krn
# panjang, lalu disodorkan ke AI sbg "opini substantif" & memicu halusinasi
# (AI mengarang narasi "utility palsu konversi saham" dari 1 pesan spam
# generik). Pesan yg cocok pola ini dikecualikan dari top_posts SAMA
# SEKALI (bukan cuma didemosikan ke short_posts) krn isinya bukan
# representasi komunitas token ini, apapun panjangnya.
_SPAM_PATTERN = re.compile(
    r"\b(stock-backed|staking rewards?|presale|pre-sale|whitelist|airdrop|"
    r"guaranteed profit|1000x|100x gem|join (our|the) (telegram|discord)|"
    r"dm (me|us) (for|to)|click here|limited time offer|exclusive access)\b",
    re.IGNORECASE,
)
# Jumlah kutipan pump.fun yg disimpan sbg evidence utk AI (meme_context/
# thesis di ai_common.py) -- dinaikkan dari 2 ke 3 krn pump.fun sekarang
# kanal PRIORITAS narasi, wajar dpt slot kutipan lebih banyak drpd
# Reddit/News.
_TOP_POSTS_CAP = 3


def _headers() -> Dict[str, str]:
    return {"x-api-key": config.PUMPFUN_COMMUNITY_API_KEY}


def _server_headers() -> Dict[str, str]:
    return {
        "x-server-key": config.PUMPFUN_COMMUNITY_SERVER_KEY,
        "x-server-secret": config.PUMPFUN_COMMUNITY_SERVER_SECRET,
    }


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    if not config.PUMPFUN_COMMUNITY_ENABLED or not config.PUMPFUN_COMMUNITY_API_KEY:
        return None
    return http.get_json(f"{BASE}{path}", params=params, headers=_headers(), timeout=config.HTTP_TIMEOUT)


def _get_server(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    if (
        not config.PUMPFUN_COMMUNITY_ENABLED
        or not config.PUMPFUN_COMMUNITY_SERVER_KEY
        or not config.PUMPFUN_COMMUNITY_SERVER_SECRET
    ):
        return None
    return http.get_json(f"{BASE}{path}", params=params, headers=_server_headers(), timeout=config.HTTP_TIMEOUT)


def _extract_rows(resp: Any, *candidate_keys: str) -> List[Dict[str, Any]]:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in candidate_keys:
            v = resp.get(k)
            if isinstance(v, list):
                return v
    return []


def community_signal(mint: str) -> Dict[str, Any]:
    """
    Return { available, member_count, post_count, total_likes, total_replies,
             distinct_posters, spam_count, avg_follower_count, posts_last24h,
             top_posts }.

    mint = token_address on-chain -- key komunitas di platform ini.
    """
    out = {
        "available": False, "member_count": 0, "post_count": 0,
        "total_likes": 0, "total_replies": 0, "distinct_posters": 0,
        "spam_count": 0, "avg_follower_count": 0.0, "posts_last24h": 0,
        "top_posts": [],
    }
    if not config.PUMPFUN_COMMUNITY_ENABLED or not config.PUMPFUN_COMMUNITY_API_KEY or not mint:
        return out
    try:
        comm = _get(f"/api/v1/communities/{mint}")
        if not comm:
            # Wajar utk token yg community-nya blm pernah dibuka siapa pun
            # (atau bukan asal pump.fun) -- degrade gracefully, BUKAN error.
            return out
        community = comm.get("community") if isinstance(comm, dict) else None
        if not isinstance(community, dict):
            log.info(
                "PumpfunCommunity: respons getCommunity tak sesuai dugaan utk mint %s...: %s",
                mint[:6], str(comm)[:500],
            )
            return out

        # TEMPORARY: log semua field asli community (docs cuma jamin
        # {id, tokenAddress, createdAt} ada, tp mgkn ada field lain spt
        # memberCount/postCount yg blm dikonfirmasi -- perlu verifikasi
        # dari log run nyata begitu API key sudah aktif).
        log.info(
            "PumpfunCommunity RAW community fields utk mint %s...: %s",
            mint[:6], community,
        )

        if not config.PUMPFUN_COMMUNITY_SERVER_KEY or not config.PUMPFUN_COMMUNITY_SERVER_SECRET:
            log.info(
                "PumpfunCommunity: PUMPFUN_COMMUNITY_SERVER_KEY/_SECRET kosong, skip baca "
                "pesan/member utk mint %s... (community metadata tetap kekonfirmasi ada, "
                "tp tak ada data pesan tanpa server key -- lihat docstring modul).",
                mint[:6],
            )
            return out

        msgs_resp = _get_server(
            f"/api/v1/communities/{mint}/messages/server", {"limit": 100, "sort": "time", "order": "desc"}
        )
        # msgs_resp is None berarti CALL GAGAL (401/403/5xx/network -- lihat
        # http.request_json, None cuma dikembalikan kalau gagal, BUKAN utk
        # respons sukses berisi array kosong spt {"messages": []}). Dulu
        # kegagalan ini disamakan diam2 dgn "community kosong" (rows=[] di
        # kedua kasus) -- available tetap True dgn semua angka 0, PADAHAL
        # itu artinya "gagal baca", bukan "genuinely tak ada pesan". Field
        # ini eksplisit dilacak biar KEDUANYA tak disamakan lagi.
        messages_call_failed = msgs_resp is None
        rows = _extract_rows(msgs_resp, "messages", "data", "items", "results")
        if msgs_resp and not rows:
            log.info(
                "PumpfunCommunity: respons getMessagesServer tak sesuai dugaan utk mint %s...: %s",
                mint[:6], str(msgs_resp)[:500],
            )
        elif rows:
            log.info(
                "PumpfunCommunity RAW message pertama (keys) utk mint %s...: %s",
                mint[:6], sorted(rows[0].keys()) if isinstance(rows[0], dict) else type(rows[0]),
            )

        members_resp = _get_server(f"/api/v1/communities/{mint}/members/server", {"limit": 100})
        members_call_failed = members_resp is None
        member_rows = _extract_rows(members_resp, "members", "data", "items", "results")

        if messages_call_failed and members_call_failed:
            # Community-nya ADA (getCommunity sukses), tapi getMessagesServer &
            # getCommunityMembersServer keduanya gagal walau x-server-key/secret
            # sudah diisi -- kemungkinan key server itu sendiri salah/invalid
            # atau blm di-approve, BUKAN bug di sini (path & header sudah
            # sesuai docs). JANGAN laporkan available=True dgn semua angka 0
            # -- itu akan kebaca "community sepi" padahal sebenarnya "kita
            # gagal baca". Degrade ke unavailable spy narrative.py netral.
            log.warning(
                "PumpfunCommunity: community ADA utk mint %s... tapi getMessagesServer & "
                "getCommunityMembersServer keduanya GAGAL walau x-server-key/secret sudah "
                "diisi -- cek lagi apakah key server valid/aktif di dashboard "
                "coincommunities.org. Degrade ke unavailable.",
                mint[:6],
            )
            return out

        now = datetime.now(timezone.utc).timestamp()
        posters = set()
        total_likes = total_replies = spam_count = 0
        follower_sum = 0
        posts_24h = 0
        posts_raw: List[Dict[str, Any]] = []
        # Filter pesan soft-deleted DULU -- semua agregat di bawah (post_count
        # termasuk) harus konsisten dihitung dari set yg SAMA (baris hidup),
        # supaya post_count tak lebih besar drpd baris yg benar2 ikut dihitung
        # (bug awal: post_count = len(rows) mentah, avg_follower_count dibagi
        # len(rows) jg, padahal pesan terhapus tak nyumbang ke pembilang).
        live_rows = [m for m in rows if isinstance(m, dict) and not m.get("deletedAt")]
        for m in live_rows:
            uid = m.get("userId")
            if uid:
                posters.add(uid)
            total_likes += int(m.get("likeCount", 0) or 0)
            total_replies += int(m.get("replyCount", 0) or 0)
            if m.get("isSpam"):
                spam_count += 1
            follower_sum += int(m.get("followerCount", 0) or 0)
            created = m.get("createdAt")
            if created:
                try:
                    ts = datetime.fromisoformat(str(created).replace("Z", "+00:00")).timestamp()
                    if (now - ts) <= 86400:
                        posts_24h += 1
                except ValueError:
                    pass
            content = m.get("content")
            if content and not m.get("isSpam") and not _SPAM_PATTERN.search(content):
                posts_raw.append({
                    "text": content, "username": m.get("username") or "?",
                    "likeCount": int(m.get("likeCount", 0) or 0),
                })

        # Konteks kualitatif: UTAMAKAN pesan SUBSTANTIF (opini/tesis member
        # soal coin-nya, spt yg diminta user), bukan cuma yg like tertinggi
        # -- sort-by-like MURNI cenderung nangkep reaksi pendek ("Tung Tung
        # Tung?", "lfg") krn itu emang lbh gampang dpt banyak like drpd
        # opini panjang, PADAHAL opini panjanglah yg kasih konteks naratif
        # asli ke AI (meme_context di ai_common.py). _OPINION_MIN_LENGTH
        # proxy kasar "ini kemungkinan opini/tesis, bukan reaksi refleks".
        posts_raw.sort(key=lambda p: p["likeCount"], reverse=True)
        long_posts = [p for p in posts_raw if len(p["text"]) >= _OPINION_MIN_LENGTH]
        short_posts = [p for p in posts_raw if len(p["text"]) < _OPINION_MIN_LENGTH]
        # Opini substantif diutamakan; slot sisa (kalau opini kurang dari
        # cap) diisi reaksi pendek ter-like tertinggi spy tetap ada konteks.
        top_posts = (long_posts + short_posts)[:_TOP_POSTS_CAP]

        out.update({
            "available": True,
            "member_count": int(community.get("memberCount") or len(member_rows)),
            "post_count": len(live_rows),
            "total_likes": total_likes,
            "total_replies": total_replies,
            "distinct_posters": len(posters),
            "spam_count": spam_count,
            "avg_follower_count": round(follower_sum / len(live_rows), 1) if live_rows else 0.0,
            "posts_last24h": posts_24h,
            "top_posts": top_posts,
        })
        log.info(
            "PumpfunCommunity OK utk mint %s...: %d post / %d member / %d wallet unik / "
            "%d like / %d spam (24j: %d post baru)",
            mint[:6], out["post_count"], out["member_count"], out["distinct_posters"],
            out["total_likes"], out["spam_count"], out["posts_last24h"],
        )
    except Exception as e:  # noqa: BLE001
        log.info("PumpfunCommunity gagal utk mint %s...: %s (degrade)", mint[:6], e)
    return out
