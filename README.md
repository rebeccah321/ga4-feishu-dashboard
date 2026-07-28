# GA4 GitHub Dashboard

This repo is the GitHub-first GA4 analytics pipeline.

It pulls GA4 weekly, normalizes the data, renders a static dashboard, and publishes
everything to GitHub Pages. Feishu can consume the published URL or data files
without needing Feishu Base permissions.

## Output

After each run:

- `dashboard/index.html` is the static dashboard.
- `dashboard/data/latest.json` is the stable JSON feed for Feishu or other tools.
- `dashboard/data/latest.csv` is the stable CSV feed.
- `data/raw/` stores raw GA4 API responses.
- `data/normalized/` stores normalized report CSV snapshots.

When GitHub Pages is enabled, the public URLs are:

```text
https://<github-user-or-org>.github.io/<repo-name>/
https://<github-user-or-org>.github.io/<repo-name>/data/latest.json
https://<github-user-or-org>.github.io/<repo-name>/data/latest.csv
```

## GitHub Setup

Push this folder as the root of a GitHub repo.

Add these repository secrets:

```text
GA4_PROPERTY_ID=258704823
GA4_SERVICE_ACCOUNT_JSON=<full service account json>
```

Then enable GitHub Pages:

1. Open repo `Settings`.
2. Open `Pages`.
3. Set source to `GitHub Actions`.
4. Run `Weekly GA4 Dashboard` manually once from the `Actions` tab.

The workflow also runs every Monday at 07:00 Asia/Shanghai.

## Local Run

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
./scripts/weekly_update.sh --mock --fetch-only
```

Open:

```text
dashboard/index.html
```

## Real GA4 Run

Fill `.env`:

```text
GA4_PROPERTY_ID=258704823
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
GA4_API_TRANSPORT=rest
```

Then run:

```bash
./scripts/weekly_update.sh --fetch-only
```

## Feishu Consumption

Use Feishu only as the display/notification layer:

- Embed the GitHub Pages dashboard URL in a Feishu document.
- Paste the `latest.json` or `latest.csv` URL into a Feishu automation or import flow.
- Later, if Feishu Base permissions are approved, run the optional Base sync scripts.

The Feishu Base scripts remain in `scripts/setup_base.py` and `push-base`, but they
are no longer on the critical path.

## Feishu Base Import Package

To generate a package that can be imported directly into Feishu Base:

```bash
python3 scripts/export_feishu_base_import.py
```

The package is written to:

```text
exports/feishu-base-import/
```

Main files:

- `seeed_解决方案增长_多维表格导入.xlsx`: import this first. It contains eight sheets for Base tables.
- `seeed_解决方案增长_飞书多维表格导入包.zip`: the same Excel, CSV tables, and README bundled for upload.
- `01_落地页官网增长看板.csv` to `08_字段说明.csv`: import these one by one if Feishu does not split the Excel sheets automatically.

The generated tables cover:

- `01_落地页官网增长看板`: Seeed solutions landing page growth summary for the Chinese entry page, English fallback page, and 12 solution pages.
- `02_单方案流量明细`: one row per requested solution with latest week, solution name, landing page views, unique visitors, average engagement time, engagement rate, CTA clicks, form submissions, and main traffic source.
- `03_流量来源渠道`: top 5 GA4 channel groups by views.
- `04_热门页面与行为`: top pages and behavior metrics.
- `05_看板指标总览`: GA4 traffic overview, traffic quality, source, behavior, and conversion metrics.
- `06_社媒平台表现`: retained platform-level social media fields for LinkedIn, X, FB, 小红书, and 抖音.
- `07_社媒内容表现`: retained post-level social media fields for deciding what to publish and where.
- `08_字段说明`: field definitions and maintenance notes for Feishu Base.

Current solution URL coverage:

- Chinese entry: `https://www.seeedstudio.com.cn/category/solutions-zh-hans`
- English fallback entry: `https://www.seeed.cc/category/solutions`
- The 12 requested solution pages are matched by their `/solutions/...` slugs.
- If the current GA4 export has no matching solution rows, the generated tables explicitly mark those rows as `GA4未命中` instead of inventing traffic data. In that case, confirm whether GA4 property `258704823` covers `seeed.cc` and `seeedstudio.com.cn`, then run a `/solutions` filtered pull.
