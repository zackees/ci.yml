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
matrix expansion must all remain distinguishable. The act2 PR #30 implementation
is the candidate to qualify; its existence does not establish deployment.

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
comparisons/`contains`, boolean grouping and the declared receipt event.
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
stacked on PR #30) adds `act --ci-capabilities`, a query that starts no
workflow/container/cache server and fetches no version notices. Its schema-1
JSON contains `producer = "act2"`, the compiled `version`, and capability names
`qualified-job-identity-v1` and `step-stage-result-v1`. The first describes the
ordered `jobIdentity` event components; the second describes Main/Pre/Post
step IDs and terminal outcomes. Bosn must request structured verbose events,
validate this query from the same digest-verified binary it will execute, and
refuse missing/incompatible contracts before building. The query establishes
no source proof or passing result. The compiled candidate query and focused
CLI/identity tests pass; Bosn consumption and a compatible release are pending.

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
| Local unchanged invocation skips | Real generic invocation twice; same graph executed once, unchanged HEAD and original evidence age | Focused lane regression tests pass; Bosn pilot pending |
| Invalidated evidence reruns | Expired receipt, changed tools/inputs, explicit rerun, tampering and omitted/failed checks | Expiry, tool change and `--no-cache` regression tests pass; remaining cases pending |
| Qualified execution identities | Direct/nested/same-name and caller/leaf matrix conformance, incomplete receipt rejected | Shared resolver/verifier fixture tests pass; act2 PR #30 open; deployed qualification pending |
| Persistent local compiler objects | Two fresh engines, restored prior objects and retained new publications; classified residual misses | Pending |
| Hosted PR skip | Actual stamped PR head, verifier outputs, precisely skipped covered jobs, successful aggregator | Pending |
| Hosted rejection and full runs | Wrong tree/parents, stale evidence, untrusted writer/fork, changed surfaces, audit and release | Pilot evidence pending |
| Independent consumer | Same generic tool runs template-python-rust-cmd without clud code | Pending |
| Interruption and concurrency | Cancellation, server outage, restart, simultaneous ordinary calls and old generations | Pending |
| Cost and diagnostics | Cold/warm/reuse timings, two real edits, original coverage, aggregate state roots and bounded CLI visibility | Pending |

Policy status tables must continue to distinguish implemented enforcement from
candidate rules. Existing workflows are the integration surface; this design
does not require adding another workflow or replacing the original test coverage.
