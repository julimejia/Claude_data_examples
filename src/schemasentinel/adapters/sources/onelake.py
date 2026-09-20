from __future__ import annotations

from collections.abc import Callable

from deltalake import DeltaTable

from schemasentinel.adapters.sources.delta import DeltaSource
from schemasentinel.domain.models import SchemaSnapshot

_SCOPE = "https://storage.azure.com/.default"
_HOST = "onelake.dfs.fabric.microsoft.com"


def default_token_provider() -> str:
    """Bearer token for OneLake via azure-identity (optional ``[onelake]`` extra)."""
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise RuntimeError(
            "OneLake support needs the optional extra: pip install 'schemasentinel[onelake]'"
        ) from exc
    return DefaultAzureCredential().get_token(_SCOPE).token


def resolve_uri(ref: str) -> str:
    """Accept a full ``abfss://`` URI or ``<workspace>/<item>/<path...>`` shorthand."""
    if ref.startswith("abfss://"):
        return ref
    parts = ref.strip("/").split("/", 2)
    if len(parts) < 3 or not all(parts):
        raise ValueError(
            f"invalid OneLake reference {ref!r}: expected abfss://... or "
            "<workspace>/<item>/<path>, e.g. ws/lake.Lakehouse/Tables/orders"
        )
    workspace, item, path = parts
    return f"abfss://{workspace}@{_HOST}/{item}/{path}"


class OneLakeSource:
    """SchemaSource for Delta tables in Microsoft Fabric OneLake (Delta over ABFSS).

    Reads only the transaction log, like ``DeltaSource``. ``token_provider`` and ``opener``
    are injectable so the storage layer can be faked in tests.
    """

    def __init__(
        self,
        token_provider: Callable[[], str] | None = None,
        opener: Callable[..., DeltaTable] = DeltaTable,
    ) -> None:
        self._token_provider = token_provider or default_token_provider
        self._opener = opener

    def snapshot(self, ref: str, *, version: str | None = None) -> SchemaSnapshot:
        uri = resolve_uri(ref)
        options = {"bearer_token": self._token_provider(), "use_fabric_endpoint": "true"}
        return DeltaSource(options, self._opener).snapshot(uri, version=version)
