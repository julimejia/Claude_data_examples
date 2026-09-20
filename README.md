# SchemaSentinel

Detects schema drift between two versions of a tabular dataset (CSV, Parquet, Delta),
classifies each change as breaking or non-breaking, explains the impact and proposes a
migration as DDL. Deterministic core, validated LLM enrichment, read-only on data.

Status: under construction. See `sdd/` for the constitution, requirements and architecture.

## OneLake (Microsoft Fabric) source

Optional extra: `pip install 'schemasentinel[onelake]'` (adds `azure-identity`).

`OneLakeSource` reads the Delta transaction log of a Fabric table over ABFSS; no data is read.
References are either `abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<item>/<path>` or the
shorthand `<workspace>/<item>/<path>` (e.g. `ws/lake.Lakehouse/Tables/orders`). Authentication uses
`DefaultAzureCredential` (e.g. `az login`, or `AZURE_CLIENT_ID/_TENANT_ID/_CLIENT_SECRET`); the
identity needs read access to the workspace. Tests use a fake storage layer; recordings against a
real Fabric tenant are pending.

## Development
```
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check
```
