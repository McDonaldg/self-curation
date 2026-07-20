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
