import json
from datetime import datetime, timedelta
from pathlib import Path

from newsdesk import DATA_DIR
from newsdesk.models import Item

MAX_RUNS_KEPT = 90


class Store:
    """JSONL item archive (one file per month of fetch) plus small JSON state files."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.dir = data_dir
        self.items_dir = data_dir / "items"
        self.items_dir.mkdir(parents=True, exist_ok=True)

    # --- items ---------------------------------------------------------------
    def _month_files(self) -> list[Path]:
        return sorted(self.items_dir.glob("*.jsonl"))

    def load_items(self, since: datetime | None = None) -> list[Item]:
        items: dict[str, Item] = {}
        files = self._month_files()
        if since:
            files = [f for f in files if f.stem >= since.strftime("%Y-%m")]
        for path in files:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        item = Item.model_validate_json(line)
                        items[item.id] = item  # later lines supersede earlier ones
        out = list(items.values())
        if since:
            out = [i for i in out if i.fetched >= since or i.published >= since]
        return out

    def append_items(self, items: list[Item]) -> None:
        by_month: dict[str, list[Item]] = {}
        for item in items:
            by_month.setdefault(item.fetched.strftime("%Y-%m"), []).append(item)
        for month, batch in by_month.items():
            with open(self.items_dir / f"{month}.jsonl", "a", encoding="utf-8", newline="\n") as f:
                for item in batch:
                    f.write(item.model_dump_json(exclude_none=True) + "\n")

    # --- small JSON state ----------------------------------------------------
    def _read(self, name: str, default):
        path = self.dir / name
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, name: str, data) -> None:
        path = self.dir / name
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    def load_seen(self) -> dict[str, str]:
        return self._read("seen.json", {})

    def save_seen(self, seen: dict[str, str], now: datetime, retention_days: int) -> None:
        cutoff = (now - timedelta(days=retention_days)).isoformat()
        self._write("seen.json", {k: v for k, v in seen.items() if v >= cutoff})

    def load_fetch_state(self) -> dict:
        return self._read("fetch_state.json", {})

    def save_fetch_state(self, state: dict) -> None:
        self._write("fetch_state.json", state)

    def load_stats(self) -> dict:
        return self._read("stats.json", {"runs": []})

    def add_run_stats(self, run: dict) -> None:
        stats = self.load_stats()
        stats["runs"] = (stats["runs"] + [run])[-MAX_RUNS_KEPT:]
        self._write("stats.json", stats)
