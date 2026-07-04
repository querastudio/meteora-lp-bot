/**
 * Meteora relay — Cloudflare Worker (GRATIS).
 *
 * Kenapa perlu: Cloudflare Meteora memblokir IP data-center (runner GitHub Actions
 * dapat 404). Worker ini jalan di jaringan Cloudflare, jadi request-nya ke Meteora
 * tidak kena blokir IP. Bot memakainya sebagai relay lewat env METEORA_PROXY.
 *
 * Keamanan: HANYA meneruskan ke host dlmm-api.meteora.ag (bukan open proxy).
 * Cache 30 detik untuk menghemat rate limit Meteora (30 RPS).
 *
 * Cara pakai (lihat README bagian "Cloudflare Worker relay"):
 *   1. Deploy Worker ini (dashboard Cloudflare -> Workers -> Create).
 *   2. Salin URL Worker (mis. https://meteora-relay.NAMA.workers.dev).
 *   3. Set repo Variable METEORA_PROXY = https://meteora-relay.NAMA.workers.dev/?url={url}
 *      (biarkan teks {url} apa adanya — bot yang mengisinya).
 */
export default {
  async fetch(request) {
    const reqUrl = new URL(request.url);
    const target = reqUrl.searchParams.get("url");
    if (!target) {
      return new Response("missing ?url=", { status: 400 });
    }

    let t;
    try {
      t = new URL(target);
    } catch (e) {
      return new Response("bad url", { status: 400 });
    }

    // Keamanan: hanya izinkan API Meteora (jangan jadi open proxy).
    if (t.hostname !== "dlmm-api.meteora.ag") {
      return new Response("forbidden host", { status: 403 });
    }

    const upstream = await fetch(t.toString(), {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        Accept: "application/json, text/plain, */*",
        Referer: "https://app.meteora.ag/",
        Origin: "https://app.meteora.ag",
      },
      // Cache di edge Cloudflare selama 30 detik (hemat rate limit Meteora).
      cf: { cacheTtl: 30, cacheEverything: true },
    });

    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") || "application/json",
        "access-control-allow-origin": "*",
        "cache-control": "public, max-age=30",
      },
    });
  },
};
