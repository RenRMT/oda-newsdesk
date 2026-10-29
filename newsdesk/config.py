from functools import cache
from pathlib import Path

import yaml

from newsdesk import CONFIG_DIR
from newsdesk.models import Source


def _load(name: str, config_dir: Path = CONFIG_DIR):
    with open(config_dir / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


@cache
def settings() -> dict:
    return _load("settings.yaml")


@cache
def sources() -> tuple[Source, ...]:
    return tuple(Source(**s) for s in _load("sources.yaml"))


@cache
def keywords() -> dict:
    return _load("keywords.yaml")


@cache
def tracks() -> dict:
    return _load("tracks.yaml")


@cache
def calendar() -> list[dict]:
    return _load("calendar.yaml")
