from datetime import datetime
from typing import Literal
from urllib.parse import quote_plus

from pydantic import BaseModel, Field, model_validator

Track = Literal["aid", "data-ai", "nl"]
LLMStatus = Literal["ok", "skipped", "refused", "invalid", "error"]


GNEWS_LOCALE = {"en": "hl=en&gl=US&ceid=US:en", "nl": "hl=nl&gl=NL&ceid=NL:nl"}


class Source(BaseModel):
    id: str
    name: str
    type: Literal["rss", "reliefweb", "gnews"]
    url: str | None = None
    q: str | None = None  # gnews search query
    query: str | None = None  # reliefweb API query
    lang: Literal["en", "nl"] = "en"
    tracks: list[Track] = Field(default_factory=list)
    prefilter: bool = True
    weight: float = 1.0
    publisher: str | None = None  # None on gnews = take publisher per item
    enabled: bool = True

    @model_validator(mode="after")
    def _gnews_url(self):
        if self.type == "gnews" and not self.url:
            self.url = f"https://news.google.com/rss/search?q={quote_plus(self.q + ' when:14d')}&{GNEWS_LOCALE[self.lang]}"
        return self

    @property
    def publisher_id(self) -> str:
        return self.publisher or self.id


class RawEntry(BaseModel):
    """An entry as fetched, before normalisation."""

    source_id: str
    title: str
    url: str
    summary: str = ""
    published: datetime | None = None
    publisher: str | None = None  # overrides the source's publisher (gnews, reliefweb)
    publisher_name: str | None = None
    publisher_url: str | None = None  # publisher home page (gnews); else the item URL's host is used
    lang: str | None = None


class Hook(BaseModel):
    flag: bool = False
    angle: str = ""
    why_now: str = ""


class Enrichment(BaseModel):
    on_topic: bool = True
    aggregator: bool = False  # content farm, reposter or multi-topic roundup
    tracks: list[Track] = Field(default_factory=list)
    summary_en: str
    type: Literal["news", "report", "policy", "opinion", "event"]
    region: str = ""
    nl_flag: bool = False
    hook: Hook = Field(default_factory=Hook)


class Item(BaseModel):
    id: str
    url: str
    title: str
    source_id: str
    publisher: str
    publisher_name: str
    publisher_domain: str | None = None
    lang: str
    published: datetime
    fetched: datetime
    excerpt: str = ""
    tracks: list[Track] = Field(default_factory=list)
    relevance: dict[str, float] = Field(default_factory=dict)
    enrichment: Enrichment | None = None
    llm_status: LLMStatus = "skipped"
    llm_attempts: int = 0
    backend: str | None = None
    cluster_id: str | None = None

    @property
    def score(self) -> float | None:
        """Best aid/data-ai relevance; None if the item was never scored."""
        if not self.relevance:
            return None
        return max(self.relevance.get("aid", 0.0), self.relevance.get("data-ai", 0.0))

    def visible(self, threshold: float) -> bool:
        if self.enrichment is not None and (not self.enrichment.on_topic or self.enrichment.aggregator):
            return False
        return self.score is None or self.score >= threshold


class Cluster(BaseModel):
    id: str
    item_ids: list[str]
    publishers: list[str]
    first_seen: datetime
    last_seen: datetime
    hot_score: float = 0.0
    velocity_24h: int = 0
    headline: str = ""
    summary: str = ""
    hook_angle: str = ""
    tracks: list[Track] = Field(default_factory=list)
    llm_status: LLMStatus = "skipped"
