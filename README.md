# ODA Newsdesk

A personal newsdesk for international development cooperation and humanitarian aid, aid × data × AI, and Dutch activity in both. A GitHub Actions job fetches about 30 English and Dutch sources three times a day, filters and deduplicates the items, and publishes a static dashboard to GitHub Pages.

## Run locally

```sh
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Windows; use .venv/bin on Linux/macOS
python -m newsdesk fetch --dry-run         # per-source counts and errors, writes nothing
python -m newsdesk run                     # fetch + store in data/ + build site/
python -m http.server -d site              # view at http://localhost:8000
python -m pytest -q
```

`--data-dir` and `--site-dir` point a run somewhere other than `data/` and `site/`, which is handy for experiments.

## Configuration

| File | Purpose |
|---|---|
| `config/sources.yaml` | Feeds and Google News queries. Sites that block feed readers go through `site:` queries. |
| `config/keywords.yaml` | EN/NL keyword prefilter per track. |
| `config/tracks.yaml` | Track labels, descriptions, and seed sentences for the relevance model (phase 2). |
| `config/calendar.yaml` | Recurring dates shown under "Coming up". |
| `config/settings.yaml` | Thresholds, retention, and LLM backend (phase 2+). |

## GitHub setup

1. Push to a **public** repo. Actions minutes are free for public repos.
2. Go to Settings → Pages and set the source to **GitHub Actions**.
3. Run the `update` workflow once manually from the Actions tab.
4. Optional: to use a custom domain, set `site.cname` in `settings.yaml` and add a DNS CNAME to `<user>.github.io`.
5. Optional: if ReliefWeb approves an API appname, set it as the repository variable `RELIEFWEB_APPNAME` and enable the `reliefweb-api` source.

## Data

Items are appended to `data/items/YYYY-MM.jsonl`. Fetch state, seen ids and run stats live alongside them in `data/`. The workflow commits `data/` after every run, so the git history doubles as the archive.
