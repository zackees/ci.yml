# GitHub API broker: incremental query rewrites + cached query merging (GHAPI-001)

Status: design. Implements the candidate policy `GHAPI-001` in [policy-general.md](../policy-general.md#github-api-budget-one-controlled-query-mechanism-per-machine-candidate-ghapi-001) ([#224](https://github.com/zackees/ci.yml/issues/224)).

Implementation lives in zackees/clud: the session `gh` shim plus the always-on clud daemon, tracked in [zackees/clud#1743](https://github.com/zackees/clud/issues/1743).

## Goal

Every machine-local GitHub **read** goes through one broker. The broker keeps a durable, time-indexed cache of what it already fetched. It **rewrites** each read into the narrowest incremental query that brings the cache up to date, **merges** the delta, and answers in the shape the caller asked for. Re-querying is allowed. Re-fetching content the cache already holds is not.

## Components

```
agent / subagent / ci script / pr_merge_watch.py
        │  gh … (unchanged syntax)
        ▼
session gh shim  (clud-shim, argv[0]=gh, already installed: CLUD_GH_SHIM_TARGET)
        │ classify: READ ─────────────► daemon broker (HTTP, loopback)
        │           WRITE ─► real gh ─► then POST /gh/invalidate
        │           UNKNOWN ─► real gh (ledger: unbrokered)
        ▼
always-on clud daemon: broker
        ├─ object store   (node_id → body, updated_at, etag, terminal)
        ├─ HWM table       ((host, repo, resource, filter) → high-water mark, etag, last_ok)
        ├─ single-flight   (one in-flight upstream request per resource key)
        ├─ budget          (X-RateLimit-* → reserve floor)
        └─ ledger          (caller, endpoint, outcome)
        │  real gh (`gh api …`) as the only upstream transport, so auth/hosts are gh's
        ▼
GitHub REST
```

The broker reaches GitHub through the real `gh api` with explicit headers. Authentication, enterprise hosts and proxies therefore keep working exactly as they do for `gh`. The broker never holds a token itself.

## Classification (shim)

| Class | Matched | Action |
|---|---|---|
| READ | `gh api` with GET (no `-X`/`--method` other than GET, no `-f`/`-F` body), `gh pr view`, `gh pr checks` (non-watch), `gh pr list`, `gh run view`, `gh run list`, `gh issue view/list` | Broker; the caller's `--json`/`-q`/`--jq`/`--template` is applied by the shim to the broker's merged JSON |
| WRITE | everything with side effects (`pr merge/create/edit/comment/close`, `run rerun/cancel`, `workflow run`, `api` non-GET, `release …`) | Real `gh`, then invalidate the touched resource keys |
| UNKNOWN | anything the parser cannot classify | Real `gh`, unchanged; ledger records `unbrokered` |

Phase 1 brokers **only `gh api` GET**. That is exact by construction: the caller already names the REST endpoint, and `--jq` stays with the shim. Porcelain commands (`pr view`, `run view`, …) are added in later phases by rewriting them to REST plus a local projection that reproduces the porcelain `--json` field names.

## Incremental rewrite table

Each READ normalizes to a **resource key** `(host, owner/repo, resource, filter)`. The high-water mark (HWM) is the newest `updated_at` or `created_at` the store has *seen* for that key, never wall-clock time.

| Resource | Upstream rewrite | Terminal / frozen |
|---|---|---|
| Any single REST object (`GET /repos/…/x/{id}`) | same URL with `If-None-Match: <etag>`; `304` = cached body | object-specific (below) |
| Issue / PR comments, review comments | `…/comments?since=<hwm - overlap>&per_page=100` | never; edits move `updated_at` |
| Workflow runs list | `actions/runs?created=>=<hwm - overlap>` + per-run refresh of cached non-terminal runs | run `status=completed` |
| Run jobs | `actions/runs/{id}/jobs?per_page=100` with ETag | job `status=completed`; all jobs frozen once the run is completed |
| Check runs | `commits/{sha}/check-runs` with ETag (new head SHA = new key) | all check runs `completed` |
| PR / issue lists | `sort=updated&direction=desc&per_page=100`, page until the first item whose `updated_at <= hwm` | merged/closed PRs |
| Repo metadata, branch protection, rulesets | ETag only, TTL 1 h | — |

The overlap window is **5 s** and covers clock skew. Overlap duplicates are removed by `node_id`, never by widening the bound.

## Merge semantics

- Upsert by `node_id`; higher `updated_at` wins, and an equal `updated_at` keeps the stored body.
- A collection response is rebuilt from the store: every object in the key's membership set, ordered as the endpoint orders it (for example `created_at` ascending for comments). Pagination headers are synthesized for a single page containing everything, because callers that page will stop after one.
- Deletions are invisible to `since=`. A low-frequency reconciliation (default every 30 min for live keys, never for frozen ones) re-fetches the full collection with `If-None-Match`. A `304` costs nothing; a `200` replaces membership.
- Write invalidation marks keys stale without dropping data, so the next read is an incremental fetch rather than a full one. For example, `pr merge N` marks the PR, its checks and the repo's run list.

## Freshness contract

Each resource class has a TTL (runs/jobs 30 s, PR/checks 60 s, metadata 1 h). Within the TTL the cache answers with zero upstream requests. After it, the next read runs the incremental rewrite. `CLUD_GH_FRESH=1` (or `--fresh`) skips the TTL but still fetches incrementally. Terminal objects are never re-fetched.

## Coordination

- **Single-flight:** concurrent reads of one key wait on one upstream request.
- **Budget:** the broker records `X-RateLimit-Remaining`/`-Reset`. Below a 10% floor, background readers (waiters, watchers) are delayed to the reset, while interactive readers (a tty on stdin) go through. A read that cannot be served returns the real `gh` error. Gates fail closed.
- **Subscriptions (phase 3):** waiters subscribe to a key and are woken on change or on terminal state, so they stop polling.

## Store

SQLite in the daemon state dir (`~/.clud/state/gh-broker.sqlite`) with two tables, `objects` and `hwm`. A `ledger` table records `(ts, session, caller_cmd, key, outcome ∈ {cache, 304, incremental, full, passthrough, unbrokered}, upstream_requests, rate_remaining)`.

## Phases

1. **Read-through ETag cache for `gh api` GET.** It covers the shim classification, daemon endpoint, store, ETag/304, TTL, single-flight and the ledger. This is the largest saving for the least risk.
2. Incremental rewrites (`since=`, `created>=`, stop-at-cached paging) and merge for comments, runs, jobs and check runs. Porcelain `run view` / `pr checks` go through REST projections.
3. Subscriptions for `pr_merge_watch.py`, the budget floor and reconciliation.
4. The ci-lint `GHAPI-001` static signal.

## Acceptance (per phase, on a real machine)

- **Phase 1:** two identical `gh api repos/o/r/actions/runs/<completed id>` calls within the TTL make **one** upstream request. After the TTL the second is answered by a `304` (shown in the ledger), and its output is byte-identical to the real `gh`.
- **Phase 2:** re-reading a PR's comments after one new comment issues `?since=` and transfers only that comment, and the merged output equals a full fetch.
