#!/usr/bin/env python3
"""
Mozilla Supportフォーラムの指定スレッドを監視し、新規コメントがあれば通知するスクリプト。

JavaScript経由のボット対策を回避するため Playwright でページを読み込みます。
既知の投稿 ID を state ファイルに保存し、次回以降の実行で差分を検出します。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib import request

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

DEFAULT_URL = "https://support.mozilla.org/en-US/forums/contributors/717446"
DEFAULT_STATE_PATH = Path.home() / ".mozilla_forum_watch.json"
DEFAULT_TIMEOUT = 45_000  # milliseconds
DEFAULT_PREVIEW_CHARS = 220


@dataclass
class Post:
    post_id: int
    author: str
    time_iso: Optional[str]
    time_text: Optional[str]
    body_text: str
    permalink: str


async def fetch_posts(url: str, timeout: int, headful: bool) -> List[Post]:
    """Playwright を使ってスレッド内の全投稿を取得する。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headful)
        try:
            page = await browser.new_page()
            page.set_default_timeout(timeout)
            logging.debug("ページにアクセスします: %s", url)
            await page.goto(url, wait_until="networkidle")
            await page.wait_for_selector("ol.posts li.forum--entry")

            entries = page.locator("ol.posts > li.forum--entry")
            count = await entries.count()
            logging.debug("検出した投稿数: %s", count)

            posts: List[Post] = []
            for index in range(count):
                entry = entries.nth(index)
                id_attr = await entry.get_attribute("id")
                if not id_attr or not id_attr.startswith("post-"):
                    logging.debug("post-* 形式ではないためスキップ: %s", id_attr)
                    continue

                try:
                    post_id = int(id_attr.split("-")[-1])
                except ValueError:
                    logging.warning("投稿 ID の解析に失敗しました: %s", id_attr)
                    continue

                author = (await entry.locator(".display-name").inner_text()).strip()
                time_el = entry.locator("time")
                time_iso = await time_el.get_attribute("datetime")
                time_text = (await time_el.inner_text()).strip()
                body_text = (await entry.locator(".content").inner_text()).strip()

                posts.append(
                    Post(
                        post_id=post_id,
                        author=author,
                        time_iso=time_iso,
                        time_text=time_text,
                        body_text=body_text,
                        permalink=f"{url}#post-{post_id}",
                    )
                )

            posts.sort(key=lambda p: p.post_id)
            return posts
        finally:
            await browser.close()


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("state ファイルの読み込みに失敗したため無視します: %s", exc)
        return {}


def save_state(path: Path, data: Dict[str, Any]) -> None:
    try:
        with path.open("w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
    except OSError as exc:
        logging.error("state ファイルの保存に失敗しました: %s", exc)


def format_post(post: Post, preview_chars: int) -> str:
    snippet = textwrap.shorten(
        " ".join(post.body_text.split()),
        width=preview_chars,
        placeholder="…",
    )
    lines = [
        f"新規投稿: #{post.post_id} by {post.author}",
        f"投稿日時: {post.time_text or post.time_iso or 'N/A'}",
        f"URL: {post.permalink}",
        f"本文冒頭: {snippet}",
    ]
    return "\n".join(lines)


def send_ntfy(topic: str, post: Post, server: str, title: Optional[str]) -> None:
    server = server.rstrip("/")
    url = f"{server}/{topic}"
    message = f"{post.author} ({post.time_text or post.time_iso or 'N/A'})\\n{post.permalink}\\n\\n{post.body_text}"

    req = request.Request(
        url=url,
        data=message.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    if title:
        req.add_header("Title", title)

    try:
        with request.urlopen(req) as resp:  # noqa: S310 - urllib での簡易 POST
            logging.debug("ntfy 応答ステータス: %s", resp.status)
    except urlerror.URLError as exc:
        logging.error("ntfy 通知に失敗しました: %s", exc)


async def run(args: argparse.Namespace) -> int:
    try:
        posts = await fetch_posts(args.url, args.timeout, args.headful)
    except PlaywrightTimeoutError as exc:
        logging.error("ページの読み込みにタイムアウトしました: %s", exc)
        return 2

    if not posts:
        logging.warning("投稿が検出できませんでした。セレクタを確認してください。")
        return 1

    state = load_state(args.state_file)
    url_state = state.get(args.url, {})
    last_seen = url_state.get("last_seen_post_id")
    logging.debug("state に保存された最終投稿 ID: %s", last_seen)

    if last_seen is None and not args.notify_all:
        logging.info("初回実行のため既存投稿を既読として登録します。")
        new_posts: List[Post] = []
    else:
        threshold = -1 if args.notify_all else int(last_seen)
        new_posts = [post for post in posts if post.post_id > threshold]

    if new_posts:
        print(f"新しい投稿が {len(new_posts)} 件見つかりました。")
        for post in new_posts:
            formatted = format_post(post, args.preview_chars)
            print(formatted)
            print("-" * 60)
            if args.ntfy_topic:
                send_ntfy(args.ntfy_topic, post, args.ntfy_server, args.ntfy_title)
    else:
        print("新しい投稿はありません。")

    state[args.url] = {
        "last_seen_post_id": max(post.post_id for post in posts),
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(args.state_file, state)

    return 0 if not new_posts else len(new_posts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mozilla Supportフォーラムのコメント更新を監視します。"
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"監視対象のフォーラムURL (既定: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"前回確認した投稿IDを保存するファイル (既定: {DEFAULT_STATE_PATH})",
    )
    parser.add_argument(
        "--notify-all",
        action="store_true",
        help="初回実行時も含め、取得したすべての投稿を通知対象にします。",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=DEFAULT_PREVIEW_CHARS,
        help="本文プレビューで表示する最大文字数。",
    )
    parser.add_argument(
        "--ntfy-topic",
        help="ntfy.sh のトピック名。指定すると新規投稿を push 通知します。",
    )
    parser.add_argument(
        "--ntfy-server",
        default="https://ntfy.sh",
        help="ntfy のサーバURL (既定: https://ntfy.sh)。",
    )
    parser.add_argument(
        "--ntfy-title",
        help="ntfy 通知のタイトル (任意)。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="ページ読み込みや要素待機のタイムアウト (ミリ秒)。",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Chromium をヘッドレスではなく可視モードで起動します (デバッグ用)。",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="詳細ログを表示します。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    exit_code = asyncio.run(run(args))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
