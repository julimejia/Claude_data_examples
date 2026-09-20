# ADR-0001 — Modular monolith with hexagonal architecture

- Status: accepted
- Date: 2026-09-19
- Decided by: project owner (pre-approved in the SDD)

## Context
SchemaSentinel is a single-user, CLI-first tool. Its risky parts are external integrations:
the LLM (Claude Code CLI, replayed transcripts, fakes), data sources (local files, Delta,
OneLake) and notifications (Telegram, console). Tests and public demos must run free and
offline.

## Options
| Option | Pros | Cons |
|---|---|---|
| A. Modular monolith + ports/adapters (chosen) | Swappable integrations, offline tests, one process, simple deploy | Slight indirection overhead |
| B. Plain monolith, direct calls | Least code initially | LLM/Fabric coupling makes tests slow, costly and hard to replay |
| C. Microservices (agent, diff, notifier) | Independent scaling | Latency, ops overhead, more failure modes; no scaling need |

## Decision
Option A. Dependency direction: `adapters → application → domain`. Ports are Protocols
owned by the application layer. No DI framework; wiring in `cli.py`.

## Consequences
- Domain and use cases are testable without I/O.
- Replay adapter makes CI and demos free.
- Any new process boundary or service requires a new ADR.
