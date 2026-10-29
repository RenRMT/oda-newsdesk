# ODA Newsdesk

A personal newsdesk for international development cooperation and humanitarian aid, aid × data × AI, and Dutch activity in both. A GitHub Actions job fetches about 30 English and Dutch sources three times a day, filters and deduplicates the items, and publishes a static dashboard to GitHub Pages.

## Run locally

```sh
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Windows; use .venv/bin on Linux/macOS
python -m newsdesk fetch --dry-run         # per-source counts, relevance scores, errors; writes nothing
python -m newsdesk run                     # fetch + score + LLM-enrich + build site/
python -m newsdesk run --no-llm            # same, without the LLM step
python -m newsdesk enrich --backend ollama # (re)enrich recent items with a specific backend
python -m http.server -d site              # view at http://localhost:8000
python -m pytest -q
```

`--data-dir` and `--site-dir` point a run somewhere other than `data/` and `site/`, which is handy for experiments.

## Configuration

| File | Purpose |
|---|---|
| `config/sources.yaml` | Feeds and Google News queries. Sites that block feed readers go through `site:` queries. |
| `config/keywords.yaml` | EN/NL keyword prefilter per track. |
| `config/tracks.yaml` | Track labels, descriptions, and seed headlines for the embedding relevance score. |
| `config/calendar.yaml` | Recurring dates shown under "Coming up". |
| `config/settings.yaml` | Relevance thresholds, retention, and LLM backend, models and pricing. |
| `prompts/enrich.md` | Editorial instructions for the LLM: on-topic test, summary, type, hook criteria. |

## How items flow

1. **Fetch.** Pull every source in `sources.yaml`.
2. **Keyword filter.** Keep matching items per track. Curated sources skip this step.
3. **Deduplicate.** Drop repeated URLs and near-identical titles.
4. **Relevance score.** Compare each item with the seed headlines using a multilingual MiniLM model running locally on CPU. Items below the threshold are stored but hidden.
5. **LLM enrichment.** For relevant items, the LLM writes an English summary, decides whether the item is on topic, and assigns tracks, type and region, plus an optional blog hook. If it refuses, fails, or is unavailable, the item is still shown with its original excerpt. The Stats page shows these outcomes and the estimated cost.
6. **Build.** Generate the static site and RSS feeds, including `feed-hooks.xml`.

The LLM backend can be switched in `settings.yaml` (`llm.backend`) or per run with `--backend`. `anthropic` (Claude Haiku) needs `ANTHROPIC_API_KEY`. `ollama` needs a local Ollama server, which phase 4 sets up inside the workflow.

## GitHub setup

1. Push to a **public** repo. Actions minutes are free for public repos.
2. Go to Settings → Pages and set the source to **GitHub Actions**.
3. Add the repository secret `ANTHROPIC_API_KEY` under Settings → Secrets and variables → Actions. Without it, runs still work but skip the LLM step.
4. Run the `update` workflow once manually from the Actions tab.
5. Optional: to use a custom domain, set `site.cname` in `settings.yaml` and add a DNS CNAME to `<user>.github.io`.
6. Optional: if ReliefWeb approves an API appname, set it as the repository variable `RELIEFWEB_APPNAME` and enable the `reliefweb-api` source.

## Data

Items are appended to `data/items/YYYY-MM.jsonl`. Fetch state, seen ids and run stats live alongside them in `data/`. The workflow commits `data/` after every run, so the git history doubles as the archive.
