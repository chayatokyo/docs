## Mozilla Support フォーラム監視スクリプトの使い方

このディレクトリには、Mozilla Support コミュニティフォーラムのスレッドを監視し、新しい投稿があれば通知する `mozilla_forum_watch.py` が含まれています。

### 1. 前提条件

- Python 3.9 以上
- [Playwright](https://playwright.dev/python/) と Chromium ブラウザのインストール

```bash
pip install --user playwright
~/.local/bin/playwright install chromium
```

> `pip`・`playwright` が `$PATH` に無い場合は、`~/.local/bin` を PATH に追加してください。

### 2. 基本的な使い方

```bash
python3 scripts/mozilla_forum_watch.py
```

初回実行では既存の投稿を既読として記録し、`~/.mozilla_forum_watch.json` に最終投稿 ID を保存します。次回以降、ID が増えていれば「新しい投稿が n 件見つかりました」と表示し、投稿者・日時・本文冒頭を出力します。終了コードは新着件数（0 の場合は 0）です。

主なオプション:

- `--url`: 監視対象 URL（既定値は `https://support.mozilla.org/en-US/forums/contributors/717446`）
- `--state-file`: 既読状態の保存先（既定値 `~/.mozilla_forum_watch.json`）
- `--notify-all`: 初回実行でも全投稿を通知
- `--preview-chars`: 本文プレビューの最大文字数
- `--verbose`: ログを詳細表示
- `--headful`: Chromium をヘッドレスではなく可視モードで起動（デバッグ用）

### 3. ntfy.sh でのプッシュ通知

`--ntfy-topic` を指定すると [ntfy.sh](https://ntfy.sh/) に POST して通知できます。

```bash
python3 scripts/mozilla_forum_watch.py \
  --ntfy-topic your-topic-name \
  --ntfy-title "Mozilla Forum 更新" \
  --preview-chars 160
```

社内 ntfy など別ホストを利用する場合は `--ntfy-server https://your-ntfy.example.com` を併用してください。

### 4. 定期実行（cron 例）

30 分おきに監視し、ログをファイルへ追記する例:

```cron
*/30 * * * * PATH="$HOME/.local/bin:$PATH" \
  python3 /workspace/scripts/mozilla_forum_watch.py >> /var/log/mozilla_forum_watch.log 2>&1
```

初回に `pip install` / `playwright install` を済ませ、cron 実行ユーザーの環境に `~/.local/bin` が含まれるようにしてください。

### 5. トラブルシューティング

- **ヘッドレスでチャレンジを突破できない場合**: `--headful` を付けて目視確認、または待機時間を `--timeout 60000` のように延長します。
- **state ファイルが壊れた場合**: `~/.mozilla_forum_watch.json` を削除するか、`--notify-all` で再初期化できます。
- **ntfy 通知が届かない**: `--verbose` でログを確認し、ネットワークやトピック名を見直してください。
