"""セルフ・キュレーション・テキストメディア — M3
RSS/YouTube収集 → SQLite保存 → 埋め込みで80/20選定 → Claude APIで高密度要約 → HTML出力

実行: python main.py
フィードバック記録: python main.py feedback <article_id> up|down
必要: pip install -r requirements.txt / 環境変数 ANTHROPIC_API_KEY
"""

import json
import os
import random
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import feedparser
import numpy as np
import trafilatura
import yaml
import yt_dlp
from jinja2 import Environment, FileSystemLoader
from sentence_transformers import SentenceTransformer

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY,
            article_id INTEGER NOT NULL REFERENCES articles(id),
            rating INTEGER NOT NULL,  -- 1 = 👍, -1 = 👎
            created_at TEXT
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


def fetch_youtube_channels(conn: sqlite3.Connection, youtube: dict) -> int:
    """YouTubeチャンネル/プレイリストを巡回し、未知の動画だけDBに追加。追加件数を返す。"""
    added = 0
    ydl_opts = {
        "extract_flat": "in_playlist",
        "playlistend": 20,
        "quiet": True,
        "no_warnings": True,
    }
    for source, channel_urls in youtube.items():
        for channel_url in channel_urls:
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(channel_url, download=False)
            except Exception:
                print(f"  [warn] 取得失敗: {channel_url}")
                continue
            for entry in (info.get("entries") or [])[:20]:
                if not entry:
                    continue
                video_id = entry.get("id")
                title = entry.get("title", "(no title)")
                if not video_id:
                    continue
                link = f"https://www.youtube.com/watch?v={video_id}"
                exists = conn.execute(
                    "SELECT 1 FROM articles WHERE url = ?", (link,)
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """INSERT INTO articles
                       (url, title, source, feed_url, published, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (link, title, source, channel_url, "",
                     datetime.now(timezone.utc).isoformat()),
                )
                added += 1
    conn.commit()
    return added


def is_youtube_url(url: str) -> bool:
    return "youtube.com/" in url or "youtu.be/" in url


_VTT_TAG_RE = re.compile(r"<[^>]+>")
_VTT_TIMING_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3} -->")


def vtt_to_text(vtt: str) -> str:
    """WebVTT字幕からタイムコード等を除いた本文テキストを抽出する。
    自動生成字幕によくある連続する重複行は間引く。"""
    lines = []
    prev = None
    for raw in vtt.splitlines():
        line = _VTT_TAG_RE.sub("", raw).strip()
        if not line or line == "WEBVTT" or line.isdigit() or _VTT_TIMING_RE.match(raw):
            continue
        if line != prev:
            lines.append(line)
            prev = line
    return " ".join(lines)


def fetch_youtube_transcript(url: str, limit: int) -> str:
    """字幕(手動優先、なければ自動生成)を取得しプレーンテキストに変換。失敗時は空文字。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        ydl_opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["ja", "en"],
            "subtitlesformat": "vtt",
            "outtmpl": os.path.join(tmp_dir, "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception:
            return ""

        vtt_files = sorted(Path(tmp_dir).glob("*.vtt"))
        if not vtt_files:
            return ""
        text = vtt_to_text(vtt_files[0].read_text(encoding="utf-8", errors="ignore"))
        return text[:limit]


def fetch_body(url: str, limit: int) -> str:
    """本文抽出。失敗時は空文字。"""
    if is_youtube_url(url):
        return fetch_youtube_transcript(url, limit)
    try:
        html = trafilatura.fetch_url(url)
        if not html:
            return ""
        text = trafilatura.extract(html, include_comments=False) or ""
        return text[:limit]
    except Exception:
        return ""


# ---------- 2. 選定層 (M2: 埋め込みベクトルによる80/20選定) ----------

_embed_model: SentenceTransformer | None = None


def _get_embed_model(name: str) -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer(name)
    return _embed_model


def embed(texts: list[str], model_name: str) -> np.ndarray:
    """正規化済み埋め込みベクトルを返す(コサイン類似度 = 内積)。"""
    model = _get_embed_model(model_name)
    return model.encode(texts, normalize_embeddings=True)


def select_articles(conn: sqlite3.Connection, cfg: dict) -> list[dict]:
    sel_cfg = cfg["selection"]
    total = sel_cfg["total_articles"]
    n_ser = max(1, round(total * sel_cfg["serendipity_ratio"]))
    n_main = total - n_ser
    lo, hi = sel_cfg["serendipity_similarity_range"]

    rows = conn.execute(
        """SELECT id, url, title, source FROM articles
           WHERE used_at IS NULL
           ORDER BY fetched_at DESC LIMIT ?""",
        (sel_cfg["candidate_pool_limit"],),
    ).fetchall()
    candidates = [dict(zip(("id", "url", "title", "source"), r)) for r in rows]
    if not candidates:
        return []

    model_name = sel_cfg["embedding_model"]
    profile_vec = embed([cfg["profile"]], model_name)[0]
    title_vecs = embed([c["title"] for c in candidates], model_name)
    similarities = title_vecs @ profile_vec
    for c, sim in zip(candidates, similarities):
        c["similarity"] = float(sim)

    # 80%: プロファイルに最も近い記事
    ranked = sorted(candidates, key=lambda c: c["similarity"], reverse=True)
    main_picks = ranked[:n_main]
    picked_ids = {c["id"] for c in main_picks}

    # 20%: 類似度が中庸(=無関係でも想定内でもない)帯からランダム抽出
    ser_pool = [
        c for c in candidates
        if c["id"] not in picked_ids and lo <= c["similarity"] <= hi
    ]
    random.shuffle(ser_pool)
    ser_picks = ser_pool[:n_ser]

    # 帯の該当記事が足りない場合は帯の中心に近い順で補充
    if len(ser_picks) < n_ser:
        chosen_ids = picked_ids | {c["id"] for c in ser_picks}
        mid = (lo + hi) / 2
        fallback = sorted(
            (c for c in candidates if c["id"] not in chosen_ids),
            key=lambda c: abs(c["similarity"] - mid),
        )
        ser_picks += fallback[: n_ser - len(ser_picks)]

    for c in main_picks:
        c["source"] = "main"
    for c in ser_picks:
        c["source"] = "serendipity"

    return main_picks + ser_picks


# ---------- フィードバック記録 ----------

def record_feedback(conn: sqlite3.Connection, article_id: int, rating: int) -> None:
    conn.execute(
        "INSERT INTO feedback (article_id, rating, created_at) VALUES (?, ?, ?)",
        (article_id, rating, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


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

    by_url = {a["url"]: a for a in articles}
    for item in digest.get("articles", []):
        src = by_url.get(item.get("url"))
        item["source"] = src["source"] if src else "main"
        item["id"] = src["id"] if src else None

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

    print("1/4 RSS/YouTube収集中...")
    added = fetch_feeds(conn, cfg["feeds"])
    added += fetch_youtube_channels(conn, cfg.get("youtube") or {})
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


def feedback_cli(article_id: str, rating: str) -> None:
    if rating not in ("up", "down"):
        sys.exit("使い方: python main.py feedback <article_id> up|down")
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    record_feedback(conn, int(article_id), 1 if rating == "up" else -1)
    print(f"記録しました: article_id={article_id} rating={rating}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "feedback":
        if len(sys.argv) != 4:
            sys.exit("使い方: python main.py feedback <article_id> up|down")
        feedback_cli(sys.argv[2], sys.argv[3])
    else:
        main()
