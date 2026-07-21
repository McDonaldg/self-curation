/**
 * GitHub Pages上の👍/👎リンク(GET /vote?id=<article_id>&rating=up|down)を受け取り、
 * GitHubの repository_dispatch イベントを発火する。
 *
 * curation.db への実際の書き込みはこのWorkerでは行わない。
 * repository_dispatch で起動する .github/workflows/feedback-dispatch.yml が
 * `python main.py feedback <article_id> <rating>` を実行してコミットする。
 *
 * 必須のシークレット/変数 (デプロイ時に設定。詳細は README.md 参照):
 *   GITHUB_TOKEN   : repository_dispatch を発火できるPAT (wrangler secret put)
 *   GITHUB_OWNER   : リポジトリオーナー (wrangler.toml の [vars])
 *   GITHUB_REPO    : リポジトリ名 (wrangler.toml の [vars])
 *   FEEDBACK_TOKEN : config.yaml の feedback.token と同じ値(任意。簡易なボット対策。
 *                    未設定なら誰でも投票できてしまうため、公開ページ運用では設定推奨)
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/vote") {
      return new Response("not found", { status: 404 });
    }
    if (request.method !== "GET") {
      return new Response("method not allowed", { status: 405 });
    }

    const id = url.searchParams.get("id") || "";
    const rating = url.searchParams.get("rating") || "";
    const token = url.searchParams.get("token") || "";

    if (env.FEEDBACK_TOKEN && token !== env.FEEDBACK_TOKEN) {
      return new Response("forbidden", { status: 403 });
    }
    if (!/^\d+$/.test(id) || (rating !== "up" && rating !== "down")) {
      return new Response("bad request: id must be numeric, rating must be up|down", {
        status: 400,
      });
    }

    const ghRes = await fetch(
      `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "self-curation-feedback-worker",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          event_type: "feedback",
          client_payload: { article_id: id, rating },
        }),
      }
    );

    if (!ghRes.ok) {
      return new Response("GitHubへの送信に失敗しました", { status: 502 });
    }

    const emoji = rating === "up" ? "👍" : "👎";
    return new Response(
      `<!doctype html><meta charset="utf-8">
<body style="font-family:sans-serif;text-align:center;padding:3rem;">
  <p style="font-size:3rem;margin:0;">${emoji}</p>
  <p>記録しました(反映まで数分かかることがあります)。このタブは閉じて大丈夫です。</p>
</body>`,
      { headers: { "content-type": "text/html; charset=utf-8" } }
    );
  },
};
