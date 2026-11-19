"""The Mongo adapters: queries here, record mapping in ``codec``."""

from celeris_bridge.adapters.mongo.client import connect, ping
from celeris_bridge.adapters.mongo.knowledge import MongoPassageIndex, MongoVisitorRecords
from celeris_bridge.adapters.mongo.stores import (
    AlreadyExists,
    MissingRecord,
    MongoConversationStore,
    MongoInstanceStore,
    MongoTemplateStore,
    ensure_indexes,
)

__all__ = [
    "AlreadyExists",
    "MissingRecord",
    "MongoConversationStore",
    "MongoInstanceStore",
    "MongoPassageIndex",
    "MongoTemplateStore",
    "MongoVisitorRecords",
    "connect",
    "ensure_indexes",
    "ping",
]
