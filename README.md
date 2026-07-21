# セルフ・キュレーション・テキストメディア (M5)

RSS/YouTube → SQLite → 埋め込みで80/20選定 → Claude API(高密度要約+クロス洞察) → HTML 1枚。

## セットアップ

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # Windowsは set / $env:
python main.py
open output/daily.html                 # macOS。Windowsは start
```

## 毎朝の自動実行

### GitHub Actions (推奨・設定済み)

`.github/workflows/daily-curation.yml` に平日6:00 JST(`cron: "0 21 * * 0-4"`, UTC基準)の
スケジュールを設定済み。リポジトリの Settings → Secrets に `ANTHROPIC_API_KEY` を登録すれば、
プッシュ後は自動的に毎朝実行され、`curation.db` の更新がコミットされ、
GitHub Pages に最新のダイジェストが公開される。`workflow_dispatch` で手動実行も可能。

### ローカルcron (代替)

```
0 6 * * 1-5 cd /path/to/self-curation && /usr/bin/python3 main.py
```

## YouTube字幕取り込み (M3)

`config.yaml` の `youtube` に main/serendipity 別でチャンネル・プレイリストのURLを
追加すると、RSSと同じ扱いでパイプラインに乗る:

```yaml
youtube:
  main:
    - https://www.youtube.com/@your-favorite-channel
```

- 動画一覧の取得は `yt-dlp` の `extract_flat` で軽量に(ダウンロードなし)
- 選定後、本文の代わりに字幕(手動優先、なければ自動生成。ja→en)を取得しテキスト化
- 字幕が存在しない動画は本文取得不可として扱われる(タイトルのみで要約)
- `youtube:` セクションが空/未設定でも既存のRSSパイプラインはそのまま動く

## フィードバック記録

各記事の下にコマンド例が表示されるので、良かった/微妙だったら記録しておく:

```bash
python main.py feedback 42 up      # 👍
python main.py feedback 42 down    # 👎
```

このコマンドは標準ライブラリのみで動くため、`pip install -r requirements.txt` を
していない環境でも記録できる。

`curation.db` の `feedback` テーブルに蓄積される(article_id, rating, created_at)。
M5から選定スコアに反映されるようになった。

### GitHub Pages上でクリックで記録する (任意)

GitHub Pagesは静的サイトなので、ページ上のボタンをクリックしただけでは
`curation.db` に書き込めない。そこで Cloudflare Workers を仲介にして、
クリック → Worker → GitHubの `repository_dispatch` → GitHub Actionsが
`curation.db` を更新、という流れにする(`cloudflare-worker/` 参照)。

```
HTMLの👍/👎リンク(GET) → Cloudflare Worker → repository_dispatch
                                             → feedback-dispatch.yml が起動
                                             → main.py feedback で記録・commit
```

セットアップ手順:

1. [Wrangler CLI](https://developers.cloudflare.com/workers/wrangler/) をインストールし
   `wrangler login`(Cloudflareアカウントが必要)
2. `cloudflare-worker/wrangler.toml` の `GITHUB_OWNER`/`GITHUB_REPO` を確認(このリポジトリ用に設定済み)
3. GitHubで、このリポジトリだけにアクセス可能な fine-grained PAT を発行
   (権限: Contents = Read and write。うまく動かない場合は Actions = Read and write も追加)
4. シークレットを設定してデプロイ:
   ```bash
   cd cloudflare-worker
   wrangler secret put GITHUB_TOKEN        # 手順3で発行したPATを貼り付け
   wrangler secret put FEEDBACK_TOKEN      # 任意。設定すると簡易的なボット対策になる
   wrangler deploy
   ```
5. デプロイで表示されたURL(`https://self-curation-feedback.<subdomain>.workers.dev`)を
   `config.yaml` の `feedback.endpoint` に `/vote` を付けて設定。`FEEDBACK_TOKEN` を設定した場合は
   `feedback.token` にも同じ値を設定する
6. `python main.py` を再実行(またはGitHub Actionsの次回実行)すると、HTML上のフィードバックが
   CLIコマンドのヒントから👍/👎のクリックリンクに変わる

**注意:** GitHub Pagesは誰でも閲覧できる公開ページのため、`FEEDBACK_TOKEN` を設定しても
URLが分かれば誰でも投票できてしまう(本物の認証ではなく、簡易的なボット対策にすぎない)。
個人の好み学習用途としてはリスクは低いが、認識した上で利用すること。

## 記事選定ロジック (M2 + M5)

`profile` とすべての未読記事タイトルを埋め込みベクトル化し、コサイン類似度でスコアリング。

- 80%: 類似度が最も高い記事(=普段の関心に近い)
- 20%: 類似度が `serendipity_similarity_range`(既定 0.3〜0.6)に入る記事からランダム抽出
  (低すぎる=無関係、高すぎる=想定内なので、あえて中間を狙う)

埋め込みはローカルの `sentence-transformers`(`paraphrase-multilingual-MiniLM-L12-v2`)で
計算するため追加のAPIキーは不要。初回実行時にモデルをダウンロードする。

### フィードバックのスコア反映 (M5)

フィード/YouTubeチャンネル(`feed_url`)単位で過去の👍/👎を集計し、類似度に重みをかける:

```
スコア = 類似度 × (1 + feedback_weight × (👍数 − 👎数) / (👍数 + 👎数))
```

- `feedback_min_samples` 件未満のフィードは中立(重み1.0)のまま
- `feedback_weight`(既定0.3)を上げるほどフィードバックの影響が強くなる
- 👎が多いフィードのスコアが下がる → 選ばれにくくなり、80/20選定にもそのまま反映される

## カスタマイズ

- `config.yaml` の feeds を差し替え(main = 普段の興味 / serendipity = 混ぜる枠)
- profile を書き換えると選定と洞察の「引き付け先」が両方変わる
- serendipity_similarity_range で中庸帯の幅を調整
- feedback_weight / feedback_min_samples でフィードバックの効き具合を調整
- body_char_limit と total_articles がトークン消費の主変数

## コスト設計(実装済み)

- 本文を冒頭2,000字に切り詰め
- システムプロンプトに Prompt Caching (cache_control)
- 記事群を1回のAPI呼び出しにまとめて渡す(個別要約×N回より安く、
  クロス洞察の質も上がる)

## 次のマイルストーン

- M6: フィードバックの蓄積量が増えたら、記事本文/クロス洞察との相関も見て
  重み付けを feed_url 単位からさらに細かく(トピック単位など)する
- 保留中: Batch API(50%オフ)— 最大24時間の非同期方式で「朝実行→即読む」運用と
  相性が悪いため、実際のAPIコストが問題になった時点で2段階フロー化を再検討
- 保留中: Notion API連携 — HTML配信で当面十分なため優先度低
