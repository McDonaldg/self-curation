"""セルフ・キュレーション・テキストメディア — M1
RSS収集 → SQLite保存 → Claude APIで高密度要約 → HTML出力

実行: python main.py
必要: pip install -r requirements.txt / 環境変数 ANTHROPIC_API_KEY
"""

import json
import os
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import feedparser
import trafilatura
import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).parent
DB_PATH = ROOT / "curation.db"


# ---------- 1. 収集層 ----------

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY,
            url TEXT UNIQUE,
            title TEXT,
            source TEXT,            -- 'main' or 'serendipity'
            feed_url TEXT,
            published TEXT,
            body TEXT,
            fetched_at TEXT,
            used_at TEXT             -- 要約に使った日付(既読管理)
        )
    """)
    conn.commit()


def fetch_feeds(conn: sqlite3.Connection, feeds: dict) -> int:
    """RSSを巡回し、未知のURLだけDBに追加。追加件数を返す。"""
    added = 0
    for source, urls in feeds.items():
        for feed_url in urls:
            parsed = feedparser.parse(feed_url)
            if parsed.bozo and not parsed.entries:
                print(f"  [warn] 取得失敗: {feed_url}")
                continue
            for entry in parsed.entries[:20]:
                link = entry.get("link")
                title = entry.get("title", "(no title)")
                if not link:
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM articles WHERE url = ?", (link,)
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """INSERT INTO articles
                       (url, title, source, feed_url, published, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (link, title, source, feed_url,
                     entry.get("published", ""),
                     datetime.now(timezone.utc).isoformat()),
                )
                added += 1
    conn.commit()
    return added


def fetch_body(url: str, limit: int) -> str:
    """本文抽出。失敗時は空文字。"""
    try:
        html = trafilatura.fetch_url(url)
        if not html:
            return ""
        text = trafilatura.extract(html, include_comments=False) or ""
        return text[:limit]
    except Exception:
        return ""


# ---------- 2. 選定層 (M1: 単純比率。M2で埋め込みに置換) ----------

def select_articles(conn: sqlite3.Connection, cfg: dict) -> list[dict]:
    total = cfg["selection"]["total_articles"]
    n_ser = max(1, round(total * cfg["selection"]["serendipity_ratio"]))
    n_main = total - n_ser

    def pick(source: str, n: int) -> list[dict]:
        rows = conn.execute(
            """SELECT id, url, title, source FROM articles
               WHERE used_at IS NULL AND source = ?
               ORDER BY fetched_at DESC LIMIT 50""",
            (source,),
        ).fetchall()
        rows = [dict(zip(("id", "url", "title", "source"), r)) for r in rows]
        random.shuffle(rows)
        return rows[:n]

    return pick("main", n_main) + pick("serendipity", n_ser)


# ---------- 3. 生成層 ----------

SYSTEM_PROMPT = """あなたは知的キュレーションメディアの編集長です。
読者プロファイル:
{profile}

以下のルールで、渡された記事群から「今朝の一冊」を編集してください。

1. 各記事について:
   - summary: 3行以内の要約(何が新しいか・なぜ重要かに絞る)
   - hidden_premise: この記事が暗黙に前提としているもの(1行)
2. 記事群全体を横断して:
   - cross_insights: 異分野の記事同士に共通する構造や意外な接点を2つ。
     読者の関心領域(3D都市データ、デリバティブ、簿記、自動化など)に
     引き付けられるならなお良い。
   - questions: 答えのない、思考を促す開いた問いを3つ。

必ず次のJSONのみを出力してください。前置きやMarkdownの```は禁止。
{{
  "articles": [
    {{"url": "...", "title": "...", "summary": "...", "hidden_premise": "..."}}
  ],
  "cross_insights": ["...", "..."],
  "questions": ["...", "...", "..."]
}}"""


def generate_digest(articles: list[dict], cfg: dict) -> dict:
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む

    payload = "\n\n---\n\n".join(
        f"URL: {a['url']}\nタイトル: {a['title']}\n"
        f"ジャンル: {'普段の関心' if a['source'] == 'main' else 'セレンディピティ枠'}\n"
        f"本文(冒頭):\n{a['body']}"
        for a in articles
    )

    resp = client.messages.create(
        model=cfg["llm"]["model"],
        max_tokens=cfg["llm"]["max_tokens"],
        # システムプロンプトは毎日ほぼ同一 → Prompt Caching でコスト削減
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT.format(profile=cfg["profile"]),
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": payload}],
    )
    text = next(block.text for block in resp.content if block.type == "text").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```")
    return json.loads(text)


# ---------- 4. 配信層 ----------

def render_html(digest: dict, articles: list[dict], cfg: dict) -> Path:
    env = Environment(loader=FileSystemLoader(ROOT / "templates"))
    tmpl = env.get_template("daily.html.j2")

    src_by_url = {a["url"]: a["source"] for a in articles}
    for item in digest.get("articles", []):
        item["source"] = src_by_url.get(item.get("url"), "main")

    out = ROOT / cfg["output"]["html_path"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        tmpl.render(
            date=datetime.now().strftime("%Y年%m月%d日"),
            digest=digest,
        ),
        encoding="utf-8",
    )
    return out


# ---------- main ----------

def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("環境変数 ANTHROPIC_API_KEY を設定してください")

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    print("1/4 RSS収集中...")
    added = fetch_feeds(conn, cfg["feeds"])
    print(f"    新規 {added} 件")

    print("2/4 記事選定中...")
    selected = select_articles(conn, cfg)
    if not selected:
        sys.exit("未読記事がありません。feedsを増やすか明日再実行してください。")
    print(f"    {len(selected)} 件を選定")

    print("3/4 本文取得中...")
    limit = cfg["llm"]["body_char_limit"]
    for a in selected:
        a["body"] = fetch_body(a["url"], limit) or "(本文取得不可。タイトルから推測して要約)"

    print("4/4 要約生成中 (Claude API)...")
    digest = generate_digest(selected, cfg)

    today = datetime.now().strftime("%Y-%m-%d")
    conn.executemany(
        "UPDATE articles SET used_at = ? WHERE id = ?",
        [(today, a["id"]) for a in selected],
    )
    conn.commit()

    out = render_html(digest, selected, cfg)
    print(f"完成: {out}  (ブラウザで開いてください)")


if __name__ == "__main__":
    main()
