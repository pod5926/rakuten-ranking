"""楽天売れ筋ランキングを毎日取得し、前日比つきの静的サイトを生成するBot。

使い方:
  python bot/generate.py fetch            # 今日のランキングを取得して data/ に保存
  python bot/generate.py fetch --sample   # APIを使わずダミーデータを保存（動作確認用）
  python bot/generate.py build            # data/ から _site/ にHTMLを生成

必要な環境変数（fetch時）:
  RAKUTEN_APP_ID       楽天ウェブサービスのアプリID
  RAKUTEN_ACCESS_KEY   同アクセスキー
  RAKUTEN_AFFILIATE_ID 楽天アフィリエイトID（任意。あると報酬リンクになる）
  SITE_URL             公開URL（例: https://user.github.io/repo）
"""

import datetime as dt
import html
import json
import os
import random
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "_site"
GENRES = json.loads((ROOT / "bot" / "genres.json").read_text(encoding="utf-8"))
JST = dt.timezone(dt.timedelta(hours=9))
API_URL = os.environ.get(
    "RAKUTEN_API_URL",
    "https://openapi.rakuten.co.jp/ichibaranking/api/IchibaItem/Ranking/20220601",
)
SITE_NAME = os.environ.get("SITE_NAME", "楽天 売れ筋ウォッチ")
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
TOP_N = 30


def today():
    return dt.datetime.now(JST).date()


# ---------- fetch ----------

def first_image(item):
    urls = item.get("mediumImageUrls") or []
    if not urls:
        return ""
    u = urls[0]
    if isinstance(u, dict):
        u = u.get("imageUrl", "")
    return u.split("?")[0] + "?_ex=200x200"


def normalize(raw_items):
    items = []
    for it in raw_items:
        if "Item" in it:  # formatVersion=1 形式にも対応
            it = it["Item"]
        items.append({
            "rank": int(it["rank"]),
            "code": it.get("itemCode", ""),
            "name": it.get("itemName", ""),
            "price": int(it.get("itemPrice", 0)),
            "url": it.get("affiliateUrl") or it.get("itemUrl", ""),
            "image": first_image(it),
            "shop": it.get("shopName", ""),
            "review_avg": float(it.get("reviewAverage") or 0),
            "review_count": int(it.get("reviewCount") or 0),
        })
    return sorted(items, key=lambda x: x["rank"])[:TOP_N]


def fetch_genre(genre):
    params = {
        "applicationId": os.environ["RAKUTEN_APP_ID"],
        "accessKey": os.environ["RAKUTEN_ACCESS_KEY"],
        "formatVersion": 2,
        "format": "json",
    }
    if genre["id"]:
        params["genreId"] = genre["id"]
    if os.environ.get("RAKUTEN_AFFILIATE_ID"):
        params["affiliateId"] = os.environ["RAKUTEN_AFFILIATE_ID"]
    req = urllib.request.Request(
        API_URL + "?" + urllib.parse.urlencode(params),
        headers={
            "User-Agent": "ranking-bot/1.0",
            "Referer": (SITE_URL or "https://github.com") + "/",
            "Origin": urllib.parse.urlsplit(SITE_URL or "https://github.com")._replace(path="").geturl(),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            body = json.load(res)
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"HTTP {err.code}: {err.read()[:300].decode('utf-8', 'replace')}") from None
    return normalize(body.get("Items", []))


def sample_genre(genre, day):
    rnd = random.Random(f"{genre['slug']}-{day}")
    pool = list(range(1, 46))
    rnd.shuffle(pool)
    return [{
        "rank": r + 1,
        "code": f"sample:{genre['slug']}-{n}",
        "name": f"【サンプル】{genre['name']}の人気商品 No.{n} 送料無料 ポイント2倍",
        "price": 980 + n * 137,
        "url": "https://www.rakuten.co.jp/",
        "image": "",
        "shop": f"サンプルショップ{n % 7}",
        "review_avg": round(3.5 + (n % 15) / 10, 2),
        "review_count": n * 23,
    } for r, n in enumerate(pool[:TOP_N])]


def cmd_fetch(sample=False, day=None):
    day = day or today()
    out_dir = DATA / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for g in GENRES:
        try:
            items = sample_genre(g, day) if sample else fetch_genre(g)
        except Exception as e:  # 1ジャンルの失敗で全体を止めない
            print(f"[warn] {g['slug']}: {e}", file=sys.stderr)
            failed += 1
            if not sample:
                time.sleep(1.2)
            continue
        (out_dir / f"{g['slug']}.json").write_text(
            json.dumps({"genre": g, "date": day.isoformat(), "items": items}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"saved {g['slug']} ({len(items)} items)")
        if not sample:
            time.sleep(1.2)  # API の秒間リクエスト制限対策
    if failed == len(GENRES):
        sys.exit("all genres failed")


# ---------- analyze ----------

def load(day, slug):
    p = DATA / day / f"{slug}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def data_days():
    return sorted(p.name for p in DATA.iterdir() if p.is_dir()) if DATA.exists() else []


def annotate(cur, prev):
    """各商品に前日比(change)と新登場フラグを付ける。"""
    prev_rank = {it["code"]: it["rank"] for it in (prev or {}).get("items", [])}
    for it in cur["items"]:
        before = prev_rank.get(it["code"])
        it["prev"] = before
        it["change"] = (before - it["rank"]) if before else None
        it["new"] = prev is not None and before is None
    risers = sorted((i for i in cur["items"] if i["change"] and i["change"] > 0), key=lambda i: -i["change"])
    news = [i for i in cur["items"] if i["new"]]
    return risers, news


# ---------- render ----------

CSS = """
:root{--bg:#fafaf7;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e6e4dd;--accent:#bf0000;--up:#0a7d3c;--down:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#eee;--muted:#a0a0a5;--card:#1e1e1e;--line:#333;--accent:#ff6b6b;--up:#4ade80;--down:#f87171}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
a{color:inherit}header,main,footer{max-width:860px;margin:0 auto;padding:16px}
header{border-bottom:1px solid var(--line)}header a{text-decoration:none;font-weight:700}
nav{display:flex;flex-wrap:wrap;gap:6px 12px;font-size:14px;margin-top:6px}nav a{color:var(--muted);font-weight:400}
h1{font-size:1.4rem;line-height:1.4}h2{font-size:1.1rem;margin-top:2em;border-left:4px solid var(--accent);padding-left:8px}
.pr{display:inline-block;font-size:12px;border:1px solid var(--muted);color:var(--muted);border-radius:4px;padding:0 6px;margin-bottom:8px}
.lead{color:var(--muted);font-size:15px}
ol.items{list-style:none;padding:0;margin:0}
.item{display:grid;grid-template-columns:44px 88px minmax(0,1fr);gap:10px;align-items:start;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;margin:8px 0}
.rank{font-weight:700;font-size:1.2rem;text-align:center}.chg{display:block;font-size:12px;font-weight:600}
.up{color:var(--up)}.down{color:var(--down)}.new{color:var(--accent)}
.item img{width:88px;height:88px;object-fit:cover;border-radius:6px;background:var(--line)}
.ph{width:88px;height:88px;border-radius:6px;background:var(--line)}
.name{font-size:14px;overflow-wrap:anywhere;line-height:1.5;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.meta{font-size:13px;color:var(--muted)}.price{font-weight:700;color:var(--fg)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
.cards a{display:block;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;text-decoration:none}
footer{font-size:13px;color:var(--muted);border-top:1px solid var(--line);margin-top:32px}
"""


def e(s):
    return html.escape(str(s), quote=True)


def short(name, n=70):
    return name if len(name) <= n else name[:n] + "…"


def jp_date(day):
    d = dt.date.fromisoformat(day)
    return f"{d.year}年{d.month}月{d.day}日（{'月火水木金土日'[d.weekday()]}）"


def page(title, body, rel, desc="", canonical=""):
    nav = " ".join(f'<a href="{rel}g/{g["slug"]}/">{e(g["name"])}</a>' for g in GENRES)
    canon = f'<link rel="canonical" href="{SITE_URL}/{canonical}">' if SITE_URL else ""
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">{canon}
<style>{CSS}</style></head><body>
<header><a href="{rel}">{e(SITE_NAME)}</a><nav>{nav}</nav></header>
<main><span class="pr">PR</span> 当サイトは楽天アフィリエイトを利用しています。
{body}</main>
<footer><p>ランキングは楽天市場のデータを毎朝自動で取得して作成しています。価格や在庫は変わることがあるため、購入前にリンク先でご確認ください。</p>
<p><a href="{rel}about.html">このサイトについて</a> ・ <a href="https://webservice.rakuten.co.jp/" target="_blank" rel="noopener">Supported by Rakuten Developers</a></p></footer>
</body></html>"""


def badge(it):
    if it.get("new"):
        return '<span class="chg new">NEW</span>'
    c = it.get("change")
    if c is None:
        return ""
    if c > 0:
        return f'<span class="chg up">↑{c}</span>'
    if c < 0:
        return f'<span class="chg down">↓{-c}</span>'
    return '<span class="chg">→</span>'


def item_li(it, show_rank=True):
    img = f'<img src="{e(it["image"])}" alt="" loading="lazy" width="88" height="88">' if it["image"] else '<div class="ph"></div>'
    stars = f'★{it["review_avg"]:.1f}（{it["review_count"]:,}件）' if it["review_count"] else ""
    return f"""<li class="item"><div class="rank">{it["rank"] if show_rank else ""}{badge(it)}</div>
<a href="{e(it["url"])}" target="_blank" rel="sponsored noopener">{img}</a>
<div><a class="name" href="{e(it["url"])}" target="_blank" rel="sponsored noopener">{e(short(it["name"]))}</a>
<div class="meta"><span class="price">{it["price"]:,}円</span> ・ {e(it["shop"])} {stars}</div></div></li>"""


def genre_body(cur, risers, news, day, prev_day):
    g = cur["genre"]
    parts = [f'<h1>楽天{e(g["name"])}ランキング {jp_date(day)}</h1>']
    summary = f'今日の楽天市場「{e(g["name"])}」売れ筋TOP{len(cur["items"])}です。'
    if prev_day:
        summary += f'前日から順位を上げた商品は{len(risers)}件、新しくランクインした商品は{len(news)}件でした。'
    parts.append(f'<p class="lead">{summary}</p>')
    if risers:
        parts.append("<h2>前日から急上昇</h2><ol class=\"items\">" + "".join(item_li(i) for i in risers[:5]) + "</ol>")
    if news:
        parts.append("<h2>今日の新登場</h2><ol class=\"items\">" + "".join(item_li(i) for i in news[:5]) + "</ol>")
    parts.append(f"<h2>TOP{len(cur['items'])}</h2><ol class=\"items\">" + "".join(item_li(i) for i in cur["items"]) + "</ol>")
    return "\n".join(parts)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def cmd_build():
    days = data_days()
    if not days:
        sys.exit("no data; run fetch first")
    OUT.mkdir(exist_ok=True)
    static = ROOT / "static"  # Search Console の確認ファイルなど、そのまま公開するファイル
    if static.exists():
        shutil.copytree(static, OUT, dirs_exist_ok=True)
    urls = [""]
    latest = days[-1]
    highlights = []
    for g in GENRES:
        slug = g["slug"]
        archive = []
        for i, day in enumerate(days):
            cur = load(day, slug)
            if not cur:
                continue
            prev_day = days[i - 1] if i else None
            risers, news = annotate(cur, load(prev_day, slug) if prev_day else None)
            title = f'楽天{g["name"]}ランキング {jp_date(day)}'
            desc = f'{jp_date(day)}の楽天市場「{g["name"]}」売れ筋TOP{len(cur["items"])}。前日比の急上昇と新登場も毎日更新。'
            body = genre_body(cur, risers, news, day, prev_day)
            write(OUT / "g" / slug / f"{day}.html", page(title, body, "../../", desc, f"g/{slug}/{day}.html"))
            urls.append(f"g/{slug}/{day}.html")
            archive.append(day)
            if day == latest:
                write(OUT / "g" / slug / "index.html",
                      page(f'楽天{g["name"]}売れ筋ランキング（毎日更新）', body
                           + "<h2>過去のランキング</h2><ul>" + "".join(f'<li><a href="{d}.html">{jp_date(d)}</a></li>' for d in reversed(archive)) + "</ul>",
                           "../../", desc, f"g/{slug}/"))
                urls.append(f"g/{slug}/")
                highlights.append((g, cur, risers, news))

    cards = "".join(
        f'<a href="g/{g["slug"]}/"><strong>{e(g["name"])}</strong><br><span class="meta">1位: {e(short(cur["items"][0]["name"], 40))}</span></a>'
        for g, cur, _, _ in highlights if cur["items"]
    )
    risers_all = sorted((i for _, _, r, _ in highlights for i in r), key=lambda i: -i["change"])[:10]
    body = f'<h1>楽天市場の売れ筋を毎朝チェック（{jp_date(latest)}更新）</h1>'
    body += '<p class="lead">楽天市場のジャンル別ランキングを毎朝自動で集計し、前日から順位を大きく上げた商品や新しくランクインした商品をまとめています。</p>'
    if risers_all:
        body += '<h2>全ジャンルの急上昇TOP10</h2><ol class="items">' + "".join(item_li(i) for i in risers_all) + "</ol>"
    body += f'<h2>ジャンル別ランキング</h2><div class="cards">{cards}</div>'
    write(OUT / "index.html", page(f"{SITE_NAME}｜楽天ランキングの急上昇・新登場を毎日更新", body, "",
                                   "楽天市場のジャンル別売れ筋ランキングを毎朝自動更新。前日から急上昇した商品や新登場の商品がひと目で分かります。"))

    about = """<h1>このサイトについて</h1>
<p>楽天ウェブサービスのAPIから楽天市場のランキングを毎朝自動で取得し、前日との比較を加えて掲載しています。</p>
<p>商品リンクは楽天アフィリエイトのリンクです。リンク先で購入されると、当サイトに紹介料が支払われることがあります。購入者の支払額は変わりません。</p>
<p>掲載情報は取得時点のものです。最新の価格・在庫・送料は楽天市場の商品ページでご確認ください。</p>"""
    write(OUT / "about.html", page(f"このサイトについて｜{SITE_NAME}", about, "", "", "about.html"))
    urls.append("about.html")

    if SITE_URL:
        sm = "".join(f"<url><loc>{e(SITE_URL + '/' + u)}</loc></url>" for u in urls)
        write(OUT / "sitemap.xml", f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>')
        write(OUT / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")
    print(f"built {len(urls)} pages into {OUT}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("fetch", "build"):
        sys.exit(__doc__)
    if args[0] == "fetch":
        day = None
        if "--date" in args:
            day = dt.date.fromisoformat(args[args.index("--date") + 1])
        cmd_fetch(sample="--sample" in args, day=day)
    else:
        cmd_build()
