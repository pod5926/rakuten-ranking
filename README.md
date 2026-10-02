# 楽天 売れ筋ウォッチ

楽天市場のジャンル別ランキングを毎朝6:30（日本時間）にGitHub Actionsで取得し、
前日比（急上昇・新登場）つきのページを生成してGitHub Pagesに公開するBotです。運用費は0円です。

## 仕組み
- `bot/generate.py fetch` … 楽天ランキングAPIから各ジャンルTOP30を取得し `data/日付/` に保存（履歴はリポジトリにコミット）
- `bot/generate.py build` … `data/` から `_site/` にHTML・sitemap.xmlを生成
- `.github/workflows/daily.yml` … 上記を毎朝実行してPagesへ公開
- ジャンルは `bot/genres.json` で増減できます

## 初期設定（リポジトリの Settings で行う）
1. Settings → Pages → Source を「GitHub Actions」にする
2. Settings → Secrets and variables → Actions に次を登録
   - `RAKUTEN_APP_ID`（楽天ウェブサービスのアプリID）
   - `RAKUTEN_ACCESS_KEY`（同アクセスキー）
   - `RAKUTEN_AFFILIATE_ID`（楽天アフィリエイトID）
3. Actions → daily-update → Run workflow で初回実行

Secrets 未登録の間はサンプルデータでページを生成します（データはコミットしません）。

## ローカルで試す
```
python bot/generate.py fetch --sample && python bot/generate.py build
```

## 稼働監視とアクセス解析
- 自動更新が失敗した日（一部ジャンルだけの失敗も含む）は、`bot-alert` ラベルのIssue「Bot停止アラート」が自動で立ちます。GitHubの通知（メール・アプリ）で届きます。次に成功した時点で自動で閉じます。
- `daily.yml` の `GA_ID`（Googleアナリティクスの測定ID）で全ページに計測タグを入れています。楽天へのリンククリックはGA4の「離脱クリック」で数えられます。
