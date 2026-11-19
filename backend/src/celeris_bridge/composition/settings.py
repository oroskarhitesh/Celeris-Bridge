"""
Configuration, read once from the environment.

Every variable is prefixed ``CB_`` so a shared shell cannot have a neighbouring
project's ``MONGO_URL`` quietly become this one's. The one exception is
``ANTHROPIC_API_KEY``: the SDK reads it itself, and this project never touches
it — a secret it does not hold is a secret it cannot log.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "settings"]


class Settings(BaseSettings):
    """What this build needs to know before it can serve anything."""

    model_config = SettingsConfigDict(
        env_prefix="CB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: Which set of adapters to build. ``memory`` needs nothing but an API key.
    adapters: Literal["memory", "mongo"] = "memory"

    #: Which agent definition to serve. A key in ``celeris_bridge.definitions``.
    definition: str = "marketing_v1"

    #: ``voyage`` | ``openai`` | ``development``. Spelled out rather than
    #: implied by a blank: nothing should end up on the word-hash embedder by
    #: forgetting to configure one.
    embeddings: Literal["voyage", "openai", "development"] = "development"
    #: Overrides the provider's default model. Changing it invalidates every
    #: stored vector, which is why the model is recorded with each passage.
    embedding_model: str = ""
    #: Overrides the provider's default size. Also invalidates the corpus.
    embedding_dimensions: int = 0

    #: The name the agent calls the company. Stands in for the workspace record
    #: until there is one.
    company: str = "Acme"

    #: Overrides the definition's own model when set, for trying another one
    #: without editing a published document.
    chat_model: str = ""

    #: Where a visitor's time is shown. The calendar's zone, until a workspace
    #: record carries it.
    time_zone: str = "Europe/London"

    #: Where uploaded documents are kept. The originals, not the passages:
    #: chunks and vectors are derived data and are rebuilt from these when
    #: the embedding model changes. Empty keeps them in the process, which
    #: is fine for a demo and loses every file on restart.
    document_root: str = ""

    #: How the crawler identifies itself. A crawler with no user agent is
    #: indistinguishable from an attack and gets blocked as one, so this
    #: should name the product and a way to contact whoever runs it.
    user_agent: str = ""
    #: Caps on one crawl. A site is never indexed whole; what these decide is
    #: which pages are kept, and breadth-first crawling keeps the shallow ones.
    crawl_max_pages: int = 40
    crawl_max_depth: int = 3
    #: Seconds between requests, raised by a site's own ``Crawl-delay``. Being
    #: rude to a customer's server is a support ticket about this product.
    crawl_delay_seconds: float = 0.5
    #: Largest page the fetcher will read. Past this it is a download.
    crawl_max_bytes: int = 4_000_000

    #: Bearer tokens for the admin API, as ``token:workspace`` entries
    #: separated by commas; a third field of ``platform`` grants the platform
    #: role. A placeholder for sessions and an identity provider, which is
    #: what the authenticator port is for -- see ``api/principal.py``.
    api_tokens: str = ""
    #: Treat every unauthenticated request as an admin of this workspace.
    #: Development only, warns on every start-up, and ignored when tokens are
    #: configured. Set it to curl an upload without building a token table.
    dev_workspace: str = ""
    #: Origins the admin application is served from, separated by commas.
    #: Explicit because these requests carry a bearer token; the widget's
    #: policy is the opposite and lives in ``api/cors.py``.
    admin_origins: str = ""

    mongo_url: str = ""
    mongo_database: str = "celeris_bridge"

    #: Longest reply the model may write. Deliberately small: these are chat
    #: replies, and a model given room to write an essay writes one.
    max_reply_tokens: int = 700

    def require_mongo(self) -> str:
        if not self.mongo_url:
            raise RuntimeError(
                'CB_ADAPTERS is "mongo" but CB_MONGO_URL is not set'
            )
        return self.mongo_url


@lru_cache(maxsize=1)
def settings() -> Settings:
    """The settings for this process.

    Cached, so configuration is read once and the same object is shared. A test
    that needs different settings calls ``settings.cache_clear()``.
    """
    return Settings()
