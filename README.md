# セルフ・キュレーション・テキストメディア (M1)

RSS → SQLite → Claude API(高密度要約+クロス洞察) → HTML 1枚。

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

## カスタマイズ

- `config.yaml` の feeds を差し替え(main = 普段の興味 / serendipity = 混ぜる枠)
- profile を書き換えると洞察の「引き付け先」が変わる
- body_char_limit と total_articles がトークン消費の主変数

## コスト設計(実装済み)

- 本文を冒頭2,000字に切り詰め
- システムプロンプトに Prompt Caching (cache_control)
- 記事群を1回のAPI呼び出しにまとめて渡す(個別要約×N回より安く、
  クロス洞察の質も上がる)

## 次のマイルストーン

- M2: 埋め込みベクトルによる80/20選定(類似度0.3〜0.6帯からserendipity抽出)、
      👍/👎フィードバックのDB記録
- M3: yt-dlp字幕取り込み、Batch API(50%オフ)
- M4: Notion API連携 or GitHub Actions化
