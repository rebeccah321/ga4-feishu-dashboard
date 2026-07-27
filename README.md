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
