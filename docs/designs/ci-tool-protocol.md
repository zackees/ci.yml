# CI execution and attestation protocol

Working specification for [issue #362](https://github.com/zackees/ci.yml/issues/362).
This is a design, not an adopted policy or a claim that the pilot is complete.
The existing format is specified in [ci-attestations.md](../ci-attestations.md).

## Ownership and invocation

`ci-lint`, distributed from `zackees/ci.yml`, owns policy interpretation,
execution-proof validation, local result reuse, commit stamping and hosted
skip decisions. The consumer invokes `ci-lint local-gate run`. Clud and
template-python-rust-cmd are consumers; neither owns the protocol implementation.

Bosn owns scheduling, source snapshots, isolated engines, cache storage and
terminal receipts. Act2 owns faithful workflow execution and executed-step
evidence. A repository declares its workflow selections and gate mappings;
it must not implement another receipt parser or turn incomplete evidence into
success. The existing ci-lint replay verifier is the validation implementation
to extend, rather than duplicating it in a new package.

The standalone runner holds an OS file lock at
`<git common dir>/ci-lint/run.lock` from cache lookup through execution and
commit stamping. Concurrent invocations, including sibling worktrees, refuse
before running a gate and may retry after the owner finishes. The lock file
persists; the kernel releases ownership on process exit, including a crash.
This prevents concurrent duplicate expensive graphs through the front door.
It does not impose a machine-wide CPU budget on independent clones or daemons;
Bosn resource admission remains responsible for that boundary.

The replay declaration accepts `report-source = "stdout"` for a command such
as `bosn ci run ... --wait --json`. Its stdout must be one terminal JSON
document. Ci-lint captures it in a private temporary file and applies the same
strict verifier used for the default `report-source = "file"` contract, whose
runner writes `CI_LINT_GATE_REPLAY_REPORT`. Stderr remains diagnostic output.
No consumer needs a script solely to copy stdout into the report file.

An isolated gate may invoke Bosn directly. With qualified replay, a provider
query and stdout receipt capture, the shared verifier checks the original
receipt's source and execution profile; a repository receipt parser is not
required. The declared test guard must still refuse developer-host execution,
and `proves-tree = true` remains mandatory. Legacy replay, exit-code-only
commands and file-forwarding wrappers do not establish this direct proof.
Every declared gate lane must directly invoke the same isolated runner and
be replay-covered before the shared verifier
can replace the repository's isolation proof; an unmapped host command cannot
inherit the exemption from another lane.

Qualified replay enrollment with `provider-query` also activates complete
portable-proof checks in the shared pre-push hook. For each exact outgoing
branch SHA, the checker reads its committed policy and definition, verifies
freshness and integrity through the existing attestation verifier, and requires
every gate in a non-optional lane. Optional omissions remain explicit; malformed,
duplicate and invalid records refuse publication. This check does not run a
build or query an execution provider. A local hook can be bypassed, so hosted
base-policy verification and a mechanically required aggregator remain necessary.

`ci-lint local-gate push --sha <full-stamped-sha> [--remote origin]` is the
shared publication operation. It executes no checks and creates no stamp. It
requires committed qualified replay enrollment, validates the full outgoing
proof through the existing pre-push checker, and holds the same clone lock
through transport. The expected SHA must still be the clean checked-out
branch head. Tree, parents and branch are checked again after validation.
Git transports that immutable SHA with an explicit remote-tip lease, allowing
an attestation message amend while refusing competing remote updates. An
unrelated remote history is refused before transport. The tool freezes exactly
one effective push URL and uses it for preflight, transport and confirmation;
a separate fetch URL is not the destination. Multiple push URLs refuse before
any remote mutation. An existing PR follows
its branch update; creating a PR or editing its metadata stays outside this
operation.

The publisher checks both remote identity and local state after transport.
An external edit observed before transport refuses the push. If an external
editor changes local source during the network operation, the command returns
failure and identifies the qualified SHA already published; it never claims
that no remote write occurred or publishes the newer unverified head. The
clone lock coordinates shared-tool invocations, not arbitrary editors. Git's
lease protects the remote branch independently. A transport failure or timeout
reports the expected SHA and an unconfirmed publication outcome; failed
confirmation after successful transport retains that SHA as well. Real
bare-Git fixtures cover
these boundaries; actual pilot PR publication is recorded below. Hosted
skip qualification remains pending.

## Execution and portable evidence

1. Load the consumer's gate declaration and validate coverage before scheduling.
   Confirm that the runner supports the required receipt and qualified job
   identity contract before an expensive run. Unsupported capability fails
   closed with a CLI diagnostic.
2. Compute lane keys using the existing lane-cache implementation: committed
   inputs, invocation, declared tool versions and environment. A fresh matching
   local pass allows skipping execution. A changed tool or expired pass does not.
   `--no-cache` executes every lane even on an already stamped commit.
3. Run missing lanes through Bosn and act2. Validate the terminal receipt's
   source tree, snapshot identity, selected workflow/event/inputs, engine,
   completion, job identities and executed mandatory checks. A successful exit
   without complete evidence cannot record a pass or create a trailer.
4. Record only proved passes. Stamp `Local-Gate:` and per-gate `Ci-Attestation:`
   trailers on the commit. A reused pass retains its original execution time;
   stamping and message-only changes do not extend its lifetime.
5. Push the stamped head. Hosted verification reads policy and definitions
   from the trusted PR base, checks writer eligibility, tree, parents, scope,
   freshness and audit selection, and emits individual `skip_<job>` decisions.
   The aggregator accepts a skipped required job only with the corresponding
   successful verification decision. Unproved jobs execute normally.

Qualified job identity is the ordered reusable-caller path plus each caller's
matrix values and the leaf's matrix values. Display names are diagnostic text,
not identity. Direct jobs, nested calls, repeated leaf names and caller/leaf
matrix expansion must all remain distinguishable. The act2 PR #30 implementation is merged into master alongside capability
PR #52. Release qualification and Bosn artifact pin adoption still precede
real adopter proof; merged source does not establish deployment.

`[gate.replay] qualified = true` enables source-derived identities. A declaration
names each `source-job` once; `key` is optional in this mode. Omitting `steps`
derives every executable check from the workflow source for each execution;
explicit step declarations retain their existing checked contract.
The shared workflow resolver expands every selected caller, prerequisite and
literal matrix leg. Literal products, exclusions and includes are supported;
expressions, duplicate legs and graphs exceeding the bounds reject before
execution. Legacy display-name declarations retain their existing behavior.

Reusable calls bind declared string and boolean inputs from typed literals,
defaults, exact parent-input references and, in qualified mode, exact caller
matrix references. Each caller leg gets its own binding. Boolean `false` stays
false; the string `"false"` is not coerced into a boolean. Missing exact input
or matrix references and type mismatches reject before running the selected
graph. Other runner-context expressions remain unknown and cannot establish
an execution identity or justify a skipped check.

Source-derived checks bind input interpolation in step names and reject
unnamed, empty, duplicate or unresolved names. Their finite condition proof
supports typed inputs, same-type equality, ASCII case-insensitive string
comparisons/`contains`, boolean grouping, unary `!` and the declared receipt event.
Negation binds before comparisons, preserves unknown values and yields a
boolean; `!cancelled()` and `!failure()` require the same successful-execution
premise as the other status functions. An unknown status can still occur in
a conjunction proven false by a bound input, without guessing that status.
Unknown contexts and mixed-type comparisons remain unknown. A known false
profile condition may exclude a step even when another operand is unknown;
an unknown condition leaves the check mandatory. This follows the relevant
[GitHub expression semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions)
without running workflow expressions. The syntax is bounded to 4096 characters
and 256 AST nodes. Constants alone never waive checks.

Failure/cancellation diagnostics are excluded only in the proof that requires
all selected validation checks to succeed. Every source-excluded check still
needs exactly one completed, skipped Main section. Missing, failed, cancelled
or unexpectedly executed excluded checks reject. Existing exact-hit save and
minimal-mode guard classifiers are shared with explicit declarations.

The terminal Bosn job document carries `identity`, an ordered list of
`{jobID, matrix}` components from root caller to leaf. `matrix` is an object or
null; an empty object normalizes to null. Object key order is irrelevant.
The leaf must also agree with the document's `job_id` and `matrix` fields.
Duplicate identities and missing required legs reject even if display names
are identical. Bounds are 32 identity components, 64 KiB per identity,
256 matrix legs per job and 512 concrete jobs per resolved selection.

Current implementation proof is fixture conformance, including the runtime
adapter reading real temporary workflow files. Runner capability negotiation
and deployed Bosn/act2 qualification are still pending. The clud source audit
now resolves the five routine selections after naming their unnamed steps,
including the three unit matrix legs. This is source conformance, not actual
execution proof. A qualified full invocation now validates the union of the
lane selections, with each shared execution identity checked once. Selections
must use the same workflow, event and inputs; the full receipt has no job
filter. The checker derives lane passes from its own successful verification,
then the gate applies the existing complete-lane accounting. No repository
script needs to create a second receipt or duplicate the job validator.
Per-lane seconds describe the full invocation's wall time, not CPU time.
Fixture integration proves one command seeds two lanes, an unchanged repeat
starts no command, and an omitted required job caches/stamps nothing.
Provider compatibility and the actual adopter run still need qualification
before replacing the private script.

The act2 candidate ([act2 PR #52](https://github.com/zackees/act2/pull/52),
merged after PR #30) adds `act --ci-capabilities`, a query that starts no
workflow/container/cache server and fetches no version notices. Its schema-1
JSON contains `producer = "act2"`, the compiled `version`, and capability names
`qualified-job-identity-v1` and `step-stage-result-v1`. The first describes the
ordered `jobIdentity` event components; the second describes Main/Pre/Post
step IDs and terminal outcomes. Bosn must request structured verbose events,
validate this query from the same digest-verified binary it will execute, and
refuse missing/incompatible contracts before building. The query establishes
no source proof or passing result. The compiled candidate query and focused
CLI/identity tests and all five PR checks pass. Bosn consumption is committed
in its adoption worktree (`128de149`): bootstrap and offline readiness share
a typed, bounded validator of the same digest-verified binary. Isolated
engine/pin/runner tests and Clippy passed. The compatible release is published;
Bosn pin adoption is under isolated qualification.

Act2's exact merged release candidate is
`0c9947b7d75a61edb6bcde3087614c00321a23a1`;
[full release qualification run 37571846804](https://github.com/zackees/act2/actions/runs/37571846804)
passed all five checks; Codespell run 37571823387 also passed on this SHA.
The clean default-branch checkout and live master matched before publishing;
the tag and release were both absent. Tag `v0.2.89-act2.11` now names this
qualified candidate. The existing
[release workflow run 37572787591](https://github.com/zackees/act2/actions/runs/37572787591)
completed successfully. The published Linux x86_64 archive was downloaded
and verified against `checksums.txt`: archive SHA-256
`753d2f1e9db1d31088ef0de372b5b124e932481d3b4987ad420de8ce278c9e64`,
extracted regular executable SHA-256
`bd9c112f9fb54eb159e6b45a1223f9ed6e84e8e5ee6f9a1cb27c1d6f913a1c4d`.
The actual executable reports version `0.2.89-act2.11` and schema-1
`act2` capabilities `qualified-job-identity-v1` and `step-stage-result-v1`,
even with a nonexistent workflow path and Docker socket. Bosn's adoption
worktree now uses these verified artifact pins; isolated checks and actual
adopter execution must qualify that change before deployment.

Clud's adoption candidate (`060373a7`) now declares the generic replay
mappings and invokes Bosn directly. Its source-derived full union is eight
concrete jobs and 96 checks; each individual lane also resolves its complete
prerequisite closure. No clud executable or private receipt parser is in
the gate path. Existing legacy parser tests remain during qualification.
No actual adopter run or hosted skip is established by these source checks.

The existing trailer stamp is a content checksum, not a digital signature.
Writer trust, trusted base policy and audit runs remain necessary. Merely
copying a trailer or receiving process exit zero is insufficient evidence.

## Independent caches and attestations

Execution attestations and compiler caches serve different purposes. A proved
pass can skip an equivalent check; compiler objects only accelerate a check
that still needs execution. A cache hit never proves that tests passed.

Local and hosted runners warm their own compiler stores. Local correctness
must not depend on downloading GitHub Actions cache payloads through the REST
metadata API. Local engines share the Bosn-owned persistent store across engine
replacement. An immutable archive's exact hit must not discard newly compiled
objects when the isolated engine exits. Hosted writers follow the declared
cache ownership and budget policy independently.

Portable commit evidence supports local-to-hosted PR reuse. Hosted successful
checks and attestation side entries support hosted reuse under their existing
verification rules. Neither metadata promotion nor a matching cache key implies
transfer of compiler payloads between environments.

## Proof required before rollout

| Requirement | Evidence required | Current pilot status |
| --- | --- | --- |
| Local unchanged invocation skips | Real generic invocation twice; same graph executed once, unchanged HEAD and original evidence age | Proven for Clud head `100ec0fc`: full run 682s, unchanged repeat 0.924s, no new run/engine and original timestamp preserved |
| Invalidated evidence reruns | Expired receipt, changed tools/inputs, explicit rerun, tampering and omitted/failed checks | Expiry, tool change and `--no-cache` regression tests pass; remaining cases pending |
| Qualified execution identities | Direct/nested/same-name and caller/leaf matrix conformance, incomplete receipt rejected | Released act2.12; Clud receipt has 26 terminal jobs with zero unqualified entries; full adversarial/provider qualification remains pending |
| Persistent local compiler objects | Two fresh engines, restored prior objects and retained new publications; classified residual misses | Cold build archive save and two fresh-engine restores proven; exact-hit delta publication, lint-only sharing and residual classification pending |
| Hosted PR skip | Actual stamped PR head, verifier outputs, precisely skipped covered jobs, successful aggregator | Pending |
| Hosted rejection and full runs | Wrong tree/parents, stale evidence, untrusted writer/fork, changed surfaces, audit and release | Pilot evidence pending |
| Independent consumer | Same generic tool runs template-python-rust-cmd without clud code | Pending |
| Interruption and concurrency | Cancellation, server outage, restart, simultaneous ordinary calls and old generations | Pending |
| Cost and diagnostics | Cold/warm/reuse timings, two real edits, original coverage, aggregate state roots and bounded CLI visibility | Pending |

Policy status tables must continue to distinguish implemented enforcement from
candidate rules. Existing workflows are the integration surface; this design
does not require adding another workflow or replacing the original test coverage.

## Second consumer qualification gaps

The independent template checkout is based on `68c580c` (main). A read-only
source audit resolves the `fast` and `dylint` prerequisite closures, including
the reusable ci-pre jobs and cache maintenance. It exposed two additional
contracts to implement before claiming portability: event-excluded prerequisite
jobs need source-proved explicit skipped-job evidence (never a missing-job
waiver). The shared verifier now derives these exclusions with the existing
bounded condition evaluator: only a source-bound false event/input guard
qualifies, the actual job must be completed/skipped with an empty section
list and matching qualified identity, and an entirely excluded selection
cannot prove a lane. Unknown or constant-only guards still require execution.
Fixture tests cover the decision; producer event qualification remains
pending. Also, `ci-ok` depends on planner-driven dynamic platform matrices which
the bounded resolver originally refused; the runtime extension below now
resolves supported expressions from proved output evidence. `reuse-decision` also has an unnamed
checkout step. Preserve the original checks and dynamic native/full lanes;
do not bypass the aggregator or silently narrow the selected graph to make
this consumer pass. The generic resolver/verifier owns these proof mechanisms;
the consumer contributes declarations and step names.

Bosn's current scheduler limit is held in one daemon's in-memory scheduler;
its per-engine CPU ceiling is sized separately. The clone-wide front-door
lock does not prove aggregate admission across independent state roots or
spares. Machine-wide CPU admission remains a required producer implementation
and pilot measurement, including the previously observed oversubscription.

## Private provider qualification in progress

Bosn source candidate `674013b` was built with Soldr in its existing isolated
test stack. Formatting, Clippy, two capability-boundary tests, 39 engine tests,
four pin tests and four Python runner-tool tests passed; ten pre-existing
live-Docker tests remain ignored. The internal service binary reports `0.1.0`;
this is a source candidate, not a released Bosn package. Its executable SHA-256
is `9ce186604c18ebaed1dd55e93b114539383f8f196b5092ac53318e8203022b4e`.

A fresh state root correctly refused to mint a registry on the shared Docker
host while existing Bosn-labelled objects were present. They were preserved.
The pilot instead uses a separate Docker daemon, capped at four CPUs and
16 GiB, with one CI slot and no spare engines. Its first Clud attempt
`8d7072ed-de44-4821-80a7-eb4cdc905bc0` failed during engine creation because the
initial Docker launch omitted the image's cgroup initialization. Zero workflow
jobs ran, the engine was removed, and the shared gate stamped no evidence.
Restoring the publisher's normal Docker entrypoint made a container with CPU
and memory limits run successfully; no application checks were substituted.

The actual full-plan qualification run
`ec5d2819-92d8-4039-8dae-9f87e642c3b3` uses Clud head
`d8155f2283c091cebeeaa71193b9507ee998d2af` and published ci-lint
`329bc01af81a4685a8fd66f8248a11526554144c`. Engine preparation passed; a direct
read-only query inside that engine independently confirmed the installed act
binary digest above and both act2.11 execution capabilities. Qualified static
job events are now present. Full coverage, valid stamping, unchanged-repeat
reuse, fresh-engine compiler warmth and hosted skips are still unproven.

### Measurement and remaining provider identity gaps

During the same private Clud run, Linux cgroup inspection confirmed the outer
Docker container's aggregate `cpu.max = "400000 100000"`: at most four CPUs
for its whole descendant tree, including every nested engine and job. The
`/init` subgroup contains only the Docker daemon and is **not** an aggregate
measurement; sample the parent container scope. At Unix time
`1791350109.07902`, that scope reported `usage_usec = 1920587159` and
`memory.peak = 8672198656`. These are cumulative since the corrected outer
Docker startup, including infrastructure initialization and the smoke check;
there was no initial per-run CPU counter sample. They cannot establish an
exact application-only cold CPU total. Subsequent warm/reuse measurements
must take before/after counters at that same parent scope. This bounds the
pilot; it does not establish a global budget for unrelated production daemons.

Source inspection during this run found two distinct facts:

- Clud's lint caller deliberately sets `save-cache = "false"` while sharing
  the build caller's namespace; the build caller retains the `auto` writer.
  This does not prove that all local PR writes are suppressed. Actual completed
  save and fresh-engine restore evidence must decide compiler persistence.
- Bosn's production planner still selects `CacheRoute::Legacy`; the coordinated
  cohort route has explicit live fixtures but is not admitted by that planner.
  A published act2 capability alone does not enroll a store into that route.

The shared lane cache currently fingerprints each declared tool with
`<tool> --version` (`ci_lint.lane_cache.ToolVersions`). For Bosn this identifies
its CLI, not the running daemon's effective act/runner pins. Replacing a daemon
with changed pins while keeping the CLI version can therefore leave the local
lane key unchanged. Binding the actual provider profile into reuse and comparing
it with the executed receipt remains required before claiming the tool/ABI
negative controls or production end-to-end qualification complete.

### Provider identity implementation evidence

Bosn candidate `fdbcde7` reuses its existing typed `ActPins` contract rather
than introducing another provider record. Runner status and new run receipts
carry the same adapter schema, act version and binary digest, engine manifest
and config digests, and runner manifest and config digests. Planning checks
captured pins before resolving an engine image. Legacy records remain readable
without pins and cannot supply provider identity for an enrolled reuse proof.

Review found that explicit retries cloned historical provider fields. A focused
isolated regression test reproduced that mismatch before the fix; retries now
capture the current provider while preserving source SHA, Git tree, snapshot
digest, event payload, parameters and retry ancestry. Formatting, all-target
Clippy and all six pin tests passed, followed by a clean review. The retained
RED and GREEN logs are `bosn-provider-retry-red.log` and
`bosn-provider-retry-green.log` in the investigation scratch directory.

This candidate has not replaced the immutable binary running the current Clud
qualification. ci-lint still needs to query the effective profile before reuse,
include it in each lane's result key, and compare it to terminal evidence before
publishing a pass. An exposed profile is not itself a successful execution.

The consumer candidate now adds opt-in `provider-query` to qualified replay.
It reads `runners.execution_pins` before lookup, fingerprints the typed pins in
every lane key, and compares the terminal receipt to the same captured profile.
It rechecks the provider before publication, including an invocation made
entirely of cached lane passes. Missing status, failed queries, duplicate JSON,
unsupported schemas and malformed digests refuse proof. Existing contracts
retain their original keys when this opt-in is absent. This enrollment must be
added to the real adopters after the current immutable candidate run finishes;
synthetic provider-change controls do not establish a deployed skip.

### Real cold run: successful checks, rejected receipt

Run `ec5d2819-92d8-4039-8dae-9f87e642c3b3` finished successfully with all
21 reported jobs completed and zero failures. Build, lint, Dylint, all three
unit-test matrix legs and CI OK passed. Engine cleanup completed; producer
total time was 2765.5 seconds, and the shared front door took 2781 seconds.
The verifier refused the report because twelve skipped jobs had no qualified
identity. It wrote no lane pass or commit stamp, and Clud remained at `d8155f2`.
This is successful workflow execution, not successful attestation qualification.

The twelve entries are the excluded native build/test callers and the harness
and integration callers, with empty sections and no matrix or identity. Bosn
settles unobserved declared placeholders to skipped after a normal provider
exit. In act2.11, the actual `jobResult = skipped` event in
`RunContext.isEnabled` is debug-only, while Bosn invokes normal `--json` logging.
A focused producer test reproduced the missing JSON at normal log level for
both direct and nested caller identities. Fixing that event emission is the
next provider qualification step; the original receipt remains retained and
the validator has not been relaxed to infer execution identity from placeholders.

The consumer binding was published as
`ec71fbc7e1839372165521f0e41a0d99fc1eea32`, after all 1160 selftests and the
mandatory stamped gate passed. Bosn provider candidate `fdbcde7` built in its
isolated test stack; its separate executable hashes to
`2a15e2e851cf944736a9cf27cc78611e6d7fa1dbea832c38b183dc88b2abb887`.
It has not replaced the immutable binary that produced this cold-run evidence.

### Hosted freshness and required-check enforcement

A diagnostic over the retained cold receipt confirmed all 96 required checks
in eight concrete jobs passed. The original report remains rejected: the
diagnostic removed the twelve unqualified placeholders only in memory and
never wrote an attestation or cached pass.

Act2 PR #53 merged as `4849612c36b906208cf8e4b9826ec9b38f7d5c70` after
all five PR checks passed. Full checks run `37580056082` qualifies that exact
merged commit before publication; no release or Bosn pin change is claimed here.

The hosted skip verifier previously checked record integrity without applying
the local lane's expiration bound. A focused regression reproduced both an
expired record and a record more than 60 seconds in the future authorizing a
skip. The consumer now applies the PR base's lane `max-age-hours` (default 24)
using the same freshness predicate as local cache lookup. Reuse keeps the
original execution timestamp; message changes cannot extend evidence lifetime.
Merged-PR cache promotion shares the predicate and keeps its existing zero
future-clock allowance. Integrity-only `attest verify` remains explicit.

The live Clud main branch reports `protected: false`, its protection endpoint
returns 404, and its repository ruleset list is empty. The workflow's CI OK
aggregator therefore is not currently a mechanically required merge check.
The pilot must establish that required check before claiming remote enforcement.

### Qualified Clud run and unchanged reuse, 2026-10-07

The preceding qualification sections record successive experiments. The
following actual run supersedes their pending act2 release and consumer
enrollment status; it does not complete the other pilot acceptance criteria.

Act2 `v0.2.89-act2.12` was published from merged commit
`4849612c36b906208cf8e4b9826ec9b38f7d5c70` after its exact-commit full checks
passed. Release run `37580974300` succeeded. The consumed Linux x86 archive
hashes to `d4fcce22efe1f6d1ccfa1117fee1dba24690fee1e5c40ee020810226ed603990`;
the extracted executable hashes to
`0b8cd125773f4a0ea6f201b49942755053f09a3312db4333826d6d2e698d5420` and
reported both required capabilities. Other platform executables were not
executed in this qualification.

Bosn candidate `7df49f2` pins those bytes and exposes the effective provider
profile. Its owned isolated build passed formatting, all-target Clippy and
six pin tests. It has not been published as a Bosn release. The pilot daemon
used executable digest
`216397a358c33e58b7860b0f4d6ac366ba462fa73fe4365a6c0a6431457ee1b2`.

Published ci-lint `6b5433e` validated Clud run
`01470562-faba-4e98-b133-dceac58b89e8` for source tree
`8c237bdb7d9ab7eb60938a913470a1cd419a272e`. All 26 jobs completed, with zero
failures, 17 qualified skips and zero unqualified terminal entries. The shared
front door proved all five lanes in 682 seconds and stamped head
`100ec0fc242b205d1612d0a3589aafa62f602f4d` with seven `Ci-Attestation:`
records, retaining execution timestamp `1791355328`. The full command took
683.528 wall seconds and consumed 1346.042 CPU seconds in the private Docker
parent cgroup. Earlier runs had warmed its local compiler cache; this is the
first qualified full run, not a clean cold-cache measurement.

The identical command took 0.924 seconds, reused all five lanes, preserved
HEAD and original timestamps, and created no new Bosn run or engine. Its
private parent CPU delta was 0.000203 seconds. A separate negative control
rejected an old provider missing execution pins before any build started.
Original logs, receipts and CPU samples remain under
`/tmp/ci-cpu-investigation/clud-362-act2-12-*`.

The exact attested head is published in draft
[Clud PR #1885](https://github.com/zackees/clud/pull/1885). Its policy/workflow
changes require ordinary hosted fallback; this initial adoption PR cannot
prove trusted hosted skipping. The Bosn release prerequisite, subsequent
attested PR, required CI OK setting, second consumer, compiler-object
persistence controls and aggregate production CPU admission remain pending.

Shared ci-lint `39d42cb` additionally rejects Boolean schema versions and
durations, after a focused two-failure regression and all 1163 selftests
passed. The valid integer records above are unaffected. The Clud adoption
currently pins `6b5433e`; final adoption must pin the fully qualified shared
tool and rerun its changed source contract before claiming final rollout.

### Publication hook and forced fresh engines, 2026-10-07

Shared ci-lint `9f45190a0d68dc9309c92ebc36f1ba7f87408041` passed all 1178
selftests and its stamped gate before publication. The Clud clone's pre-push
hook now uses a durable `uvx` launcher pinned to that commit. An actual hook
invocation accepted head `100ec0fc`. A synthetic commit with the same tree
and parents and only a valid `Local-Gate:` trailer was refused, naming all
seven missing gate attestations. That negative-control commit was never
pushed. Tests also reproduce and close the CLI's former worktree-policy bypass:
the hook reads policy from each outgoing SHA even when the worktree policy
is missing or malformed. The generic PR publication command remains pending.

Two unchanged executions used `local-gate run --no-cache --no-stamp` from the
published `6b5433e` consumer, with one owned engine active at a time and the
same four-CPU private Docker parent limit. Both proved all five lanes,
completed 26/26 jobs with zero failures, removed their engines, and preserved
head `100ec0fc` and tree `8c237bdb`.

| Bosn run | Engine ID prefix | Front-door time | Producer wall time | Private parent CPU |
| --- | --- | --- | --- | --- |
| `2eebf4ca-f0fd-4b7f-8246-7d0eb566c571` | `0c5d1bce3047` | 573s | 570.965s | 1231.739s |
| `1aca7a9e-4c35-4382-baef-a62ee06bf419` | `0c27aa4c8a05` | 601s | 599.333s | 1252.923s |

The sampling wrappers took 580.051s and 610.057s; their ten-second polling
interval includes observation delay. These are distinct fresh-engine runs,
not local attestation hits. Evidence, original receipts, full event streams,
engine identities and CPU samples are retained under
`/tmp/ci-cpu-investigation/clud-362-forced-fresh-engine-*`.

The earlier cold run `ec5d2819` reported a build-cache session with zero hits
and 451 misses, then saved the compiler archive as cache ID 6. Its rejected
execution receipt never qualified an attestation. Both fresh engines restored
the same build-cache key and reported 451 hits and zero misses in that session.
Archive ID 6 remains physically present in the private cache volume after
engine teardown. Compiler-cache data and successful gate evidence are distinct:
the cold archive can supply validated compiler objects without authorizing a
gate skip.

These session snapshots have `global-fallback` provenance and no journal;
they are not whole-workflow hit-rate totals. They prove that this saved archive
survives and serves compatible builds. They do not prove publication of new
objects following an exact-hit restore, selected-lint writer sharing,
concurrent publication, outage recovery or complete classification of residual
non-cacheable compiles/materialization failures. Those controls and two real
code edits remain required; this evidence does not complete the cache pilot.


## Selected outputs for dependency-driven graphs (candidate)

Act2 PR #54 adds `selected-job-outputs-v1`. The consumer requests only the
non-secret outputs needed by the selected source graph, using repeated
`--ci-output <qualified/job/path>:<output>` arguments. Every path component and
output name is a workflow identifier; matrix legs share a selector but emit
separate concrete identities. Bosn includes these requests in run identity and
requires the capability before workflow execution. An older `.12` pin cannot supply this contract and must refuse requests;
the published `.13` qualification is recorded below.

A successful producer emits one event per requested concrete job with
`msg = "CI output evidence"`, `ciOutputSchema = 1`, the existing `jobIdentity`,
and either `jobOutputs` (distinct string names to string values) or a value-free
`jobOutputsError`. Emission occurs after ordinary job-output interpolation and
requires successful, uncancelled real execution. Configured secrets, tokens,
and runtime masks forbid publishing matching values. Requests are limited to
256 selectors and 1024 bytes each; each emitted JSON value object is limited to
64 KiB. Missing outputs refuse the complete requested payload. This event
contract conveys execution data and cannot itself authorize an attestation.

Bosn retains schema, sequence, values and refusal reason on the same concrete
job. Missing/null payloads, conflicting fields, malformed values, unsupported
schema, unqualified identities, and duplicate events cannot preserve usable
values. A second evidence event invalidates the first, even if its fields are
null or omitted. Output observation never changes job success or completion.
Old receipts remain readable but supply no output evidence.

The shared verifier must resolve a dynamic graph in dependency order. It first
binds terminal receipt metadata to the submitted source, event, inputs and
queried provider. It then proves each output-producing job's source-derived
checks before admitting its outputs. A reusable workflow's public output must
follow its checked `workflow_call.outputs` mapping to the qualified executed
leaf. Only these proved values may resolve bounded `needs` expressions,
`fromJSON` matrices, call inputs and conditions. The existing graph expansion,
check derivation and execution proof remain the single implementations of
those responsibilities; adapters must not replicate them or inject a private
planner result as trusted policy.

Resolution must retain every selected dependency, required concrete matrix
leg and original native/full/release lane. Cycles, ambiguous matrix output
aggregation, unsupported expressions, duplicate JSON keys, oversized expansion
and missing or refused evidence reject proof. A producer-provided empty matrix
cannot remove coverage without proving the source policy that permits it.
The bounded runtime extension below implements this resolution for supported
expressions. Actual adopter and end-to-end hosted qualification remain pending.


The shared matrix expander now accepts already-proved dependency outputs for
whole-matrix or axis-level `fromJSON(needs.<job>.outputs.<name>)` expressions.
It applies the existing literal product/include/exclude rules and retains the
producer dependency and every concrete leg. Matching requires the same caller
scope and an unambiguous non-matrix producer. Duplicate JSON fields, nonfinite
values, excessive depth/size, embedded expressions and empty dynamic expansion
reject. Static callers with no output evidence continue to reject expressions.
Runtime orchestration and reusable public-output mapping are implemented
below; actual adopter qualification remains pending. No new attestation
eligibility follows from passing this primitive's conformance fixtures.


Reusable-output mapping now follows literal
`workflow_call.outputs.<name>.value = jobs.<job>.outputs.<output>` declarations
through checked local calls, including nested calls. Each mapped record retains
its original qualified executed-leaf identity and sequence. Missing mappings,
undeclared jobs/outputs, malformed definitions, foreign caller scope and
ambiguous matrix producers refuse the dependent graph. Unrequested public
outputs do not become evidence. Fixture conformance covers this binding;
the runtime extension below uses this mapping; actual second-adopter
qualification remains pending.


Runtime verification now has an explicit deferred-output state. Preflight can
defer a supported matrix expression awaiting evidence; ordinary static errors
still reject execution. The verifier freezes workflow definitions before
execution. After the terminal receipt, the same source-derived job proof
admits each producer's outputs in dependency order, then rebuilds the complete
selected graph and validates every required concrete job. Deferred state itself
never yields a pass or attestation. A queried provider predating act2.13 refuses
this path before execution; receipts containing selected outputs require the
qualified act2.13 contract. Fixture tests include a real child process and
missing/skipped planner and matrix checks, rather than simulated attestations.

The same condition interpreter now accepts proved
`needs.<declared-dependency>.outputs.<name>` values within the consumer's caller
scope. It supports string/boolean conditions and bounded
`contains(fromJSON(<output>), '<suite>')` membership in string arrays, using
ASCII case-insensitive exact element matching. Arrays are not compared by
structural equality. Missing, foreign, ambiguous or matrix-producer outputs
remain unknown and cannot excuse checks. Unsupported JSON types and embedded
expressions also remain unknown. The source-derived check contract snapshots
only outputs already accepted by dependency execution proof. Every excluded
step still requires an explicit completed/skipped Main section in the receipt;
omission, failure, success, another stage or an unfinished section rejects.
Preflight now discovers output references through the same bounded condition
parser, ignoring quoted strings. A source-derived job whose conditions require
unavailable declared dependency outputs enters the existing deferred runtime
path even without a dynamic matrix. Preflight continues checking source names
and declaration coverage; deferred state cannot itself produce a pass. Queried
providers older than act2.13 refuse before the child executes.

For concrete jobs, a bound false job condition is evaluated before matrix
expansion. It retains one nonmatrix identity whose receipt must show a completed
skipped job with no sections. This permits a policy-guarded empty dynamic
matrix; an empty matrix without that proof still rejects. Virtual reusable
callers retain their existing traversal and do not gain this exclusion. The
child-process receipt fixture preserves ordinary executed tests alongside the
excluded platform job, rejects missing/refused/early/unproved planner evidence,
and rejects a constant-false coverage waiver. Neither adopter nor hosted skip
qualification is claimed by these fixtures.

A small workflow executed with the released act2.13 Linux binary
(`48541dd9f8d579a6521dba02cbeec207c12359220c504ec3b2424dce8b79c5db`)
also confirms the producer behavior: its planner emits `lanes=[]`, an ordinary
dependent test executes successfully, and the guarded platform job emits
`jobResult=skipped` with qualified identity `platforms` and an empty matrix.
This is producer conformance, separate from a Bosn receipt or an adopter's
attestation. The local evidence bundle is
`/tmp/ci-cpu-investigation/act2-empty-matrix-362-XXXXXX/evidence.json`.

[Act2 release .13](https://github.com/zackees/act2/releases/tag/v0.2.89-act2.13)
is now published after exact merged-commit full CI
([37593921290](https://github.com/zackees/act2/actions/runs/37593921290)) and the
existing release workflow
([37595328315](https://github.com/zackees/act2/actions/runs/37595328315)). Its
consumed Linux x86_64 archive matches publisher checksums
(`a219d5539358d0df0088ca59f7e2aafde2f7d9701f92fda402c63b19116496c7`);
the executable hashes to
`48541dd9f8d579a6521dba02cbeec207c12359220c504ec3b2424dce8b79c5db` and reports
all three capabilities offline. Bosn source pin adoption is isolated-tested;
a deployed Bosn release and end-to-end hosted skip qualification remain pending.

## Bosn's shared-gate qualification

Bosn's source migration is locally qualified on tree
`c52981190f8cd4aba12225986b5aa7aae6ce1088`. The published shared tool at
`96ba9f2df5b6c16d4fc933ee9dc2dbab7c47b46e` invoked Bosn directly; the
private deployed provider used the checksum-verified act2.13 release and
reported actual binary and engine/runner image digests.

| Lane | Durable Bosn run | Shared verdict | Execution time |
| --- | --- | --- | --- |
| Rust | `771eb114-d093-4cbc-b400-8aa3851e6b52` | Passed | 370 s |
| Linux lint, policy, unit and Docker | `860765ca-7ffa-4db0-90f6-c08046ccae9b` | Passed | 610 s |

Both terminal receipts say `state=done`, `conclusion=success` and
`cleanup=removed`. The Rust receipt retains successful format/Clippy,
boundary/locked-resolution and workspace test sections. Linux retains
successful install, lint, shared policy validation and unit/Docker test
sections, with the source-excluded minimal-tier placeholder explicitly
skipped. The verifier stamped seven per-gate trailers on commit
`21ddd4303c5174314af496c10353c0e595d502b7`.

Repeating the same standalone command took 0.754 seconds and reused both
lanes without submitting another expensive graph. The already-stamped head
remained unchanged, and every original evidence timestamp and lane key was
preserved. The retained repeat report is
`/tmp/ci-cpu-investigation/bosn-362-qualified-repeat-evidence.json`; the
original gate log is `bosn-362-qualified-full-gate.log` in the same directory.
This proves local execution and result reuse on the migration candidate.
Public Bosn deployment and hosted skip enforcement remain pending.

### Second-consumer boundary still requiring implementation

A source inventory of template-python-rust-cmd confirms that ordinary fast
and Dylint selections also traverse the reusable precheck's cache-budget
and cache-janitor jobs. Those jobs declare `CI_REMOTE_ONLY`; Bosn replaces
only their steps with a diagnostic and reports `remote_only`. The shared
verifier currently requires their original successful checks, so this
consumer cannot yet qualify. A diagnostic cannot prove a remote audit.

The protocol must distinguish required execution evidence from remote
maintenance dependencies. Any future representation must retain qualified
identity and source binding, prevent remote maintenance from producing gate
attestations or planner outputs, and preserve hosted execution of those
jobs. It must keep ordinary build, unit, installed-wheel smoke and Dylint
checks mandatory. A reason-string waiver or treating the diagnostic as the
original successful job does not satisfy this boundary. The inventory is
retained in `template-362-qualification-inventory.json` under the same
local evidence directory. No second-consumer or hosted qualification is
claimed from that inventory.


## Clud publication qualification

The updated Clud diagnostic verdict rejects incomplete and engine-error
reports even when their job counts look complete. Its actual standalone
local gate, using the shared verifier at `96ba9f2`, passed all five lanes
through Bosn/act2 run `d2f6a42a-a294-4fc0-b7c6-2a56519bb4f3` in 613 seconds.
The terminal receipt contains 26 completed jobs, zero failures and
`cleanup=removed`. Its Rust unit log records the incomplete-report,
engine-error and success-count regression tests passing. An identical
invocation took 1.103 seconds, reused all five lanes and left every portable
attestation unchanged.

The published standalone tool at
`153302bab78f1753677e94ed1d4e300821cf6019` then ran
`ci-lint local-gate push --sha a5858913e44a3f88b3c46d1830866bd4b2203baf`.
GitHub's PR and commit APIs confirm that [Clud PR #1885](https://github.com/zackees/clud/pull/1885)
now names that exact stamped SHA, tree
`ee58d4c97171ca06b0c98a84a4dcdfe8ad143f67`, parent `100ec0fc`, and an unchanged
commit message containing seven gate trailers. No Clud-specific publishing
or receipt parser was invoked. Detailed local evidence is retained in
`/tmp/ci-cpu-investigation/clud-362-shared-publisher-evidence.json`, alongside
the terminal receipt, event stream and repeat report.

The pilot also configured and read back Clud main's actual branch protection:
`CI OK` from GitHub Actions app id `15368` is required, including for admins;
force pushes and deletions are disabled. `strict=false` avoids the unqueued
base-update rerun mechanism governed by GEN-006. This establishes the merge
boundary's required-check setting. It does not prove the aggregator accepted
attested skips in a hosted run.

[Hosted adoption run 37605738668](https://github.com/zackees/clud/actions/runs/37605738668)
is the ordinary PR run for this published head. Because the adoption changes
workflow and policy surfaces, its remote fallback is expected and cannot
count as hosted attestation-skip proof. A later source-only head against the
adopted base policy must prove those skips. The end-to-end pilot remains
incomplete until that evidence and the remaining issue #362 controls exist.
