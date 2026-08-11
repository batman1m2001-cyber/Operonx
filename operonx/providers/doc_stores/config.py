"""Document store configuration."""

from enum import Enum
from typing import ClassVar, Optional

from operonx.core.utils import YamlModel


class DocStoreType(Enum):
    """Supported store-of-record backends."""

    POSTGRES = "postgres"
    MONGO = "mongo"
    REDIS = "redis"


class DocStoreConfig(YamlModel):
    """Configuration for a store of record.

    Resources carry **connection config only** — no queries. This mirrors
    the split operonx already uses for LLMs, where the model is a resource
    but the prompt is a call-site argument. Business logic does not live
    in YAML.

    Attributes:
        api_type: Which backend to build.
        collection: Default table / collection, used when a call passes
            ``collection=None``.
        id_field: Default primary-key field name.
        dsn: Postgres / Mongo / Redis connection string.
        database: Mongo only — database name.
    """

    _category: ClassVar[str] = "doc_store"

    api_type: DocStoreType = DocStoreType.POSTGRES
    collection: Optional[str] = None
    id_field: str = "id"

    dsn: Optional[str] = None
    database: Optional[str] = None

    @classmethod
    def default(cls) -> "DocStoreConfig":
        """Local Postgres on the default port."""
        return cls(
            api_type=DocStoreType.POSTGRES,
            dsn="postgresql://postgres@localhost:5432/postgres",
            collection="docs",
            id_field="id",
        )
