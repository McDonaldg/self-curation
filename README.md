# セルフ・キュレーション・テキストメディア (M2)

RSS → SQLite → 埋め込みで80/20選定 → Claude API(高密度要約+クロス洞察) → HTML 1枚。

## セットアップ

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # Windowsは set / $env:
python main.py
open output/daily.html                 # macOS。Windowsは start
```

## 毎朝の自動実行 (cron 例: 平日6:00)

```
0 6 * * 1-5 cd /path/to/self-curation && /usr/bin/python3 main.py
```

## フィードバック記録

各記事の下にコマンド例が表示されるので、良かった/微妙だったら記録しておく:

```bash
python main.py feedback 42 up      # 👍
python main.py feedback 42 down    # 👎
```

`curation.db` の `feedback` テーブルに蓄積される(article_id, rating, created_at)。
将来のマイルストーンでランキング学習に使う想定。

## 記事選定ロジック (M2)

`profile` とすべての未読記事タイトルを埋め込みベクトル化し、コサイン類似度でスコアリング。

- 80%: 類似度が最も高い記事(=普段の関心に近い)
- 20%: 類似度が `serendipity_similarity_range`(既定 0.3〜0.6)に入る記事からランダム抽出
  (低すぎる=無関係、高すぎる=想定内なので、あえて中間を狙う)

埋め込みはローカルの `sentence-transformers`(`paraphrase-multilingual-MiniLM-L12-v2`)で
計算するため追加のAPIキーは不要。初回実行時にモデルをダウンロードする。

## カスタマイズ

- `config.yaml` の feeds を差し替え(main = 普段の興味 / serendipity = 混ぜる枠)
- profile を書き換えると選定と洞察の「引き付け先」が両方変わる
- serendipity_similarity_range で中庸帯の幅を調整
- body_char_limit と total_articles がトークン消費の主変数

## コスト設計(実装済み)

- 本文を冒頭2,000字に切り詰め
- システムプロンプトに Prompt Caching (cache_control)
- 記事群を1回のAPI呼び出しにまとめて渡す(個別要約×N回より安く、
  クロス洞察の質も上がる)

## 次のマイルストーン

- M3: yt-dlp字幕取り込み、Batch API(50%オフ)
- M4: Notion API連携 or GitHub Actions化
- M5: フィードバック(👍/👎)を選定スコアに反映(例: 👎が多いソースの重み低下)
