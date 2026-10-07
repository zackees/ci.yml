"""Hosted consumers share immutable-base trust and per-job attestation decisions."""

import re
from dataclasses import replace
from pathlib import Path

from ci_lint.attestations import JobDecision, decide_jobs
from ci_lint.attestations import load_definition as load_attestation_definition
from ci_lint.attestations import verify_commit as verify_attestations
from ci_lint.cargo_messages import JsonValue
from ci_lint.gate_trust import TrustInput, decide
from ci_lint.local_gate import load_gate_config_at
from ci_lint.workflow_scan import load_workflows_at
from ci_lint.workflow_replay_maintenance import non_attestable_jobs


def job_decisions(repo: Path, inp: TrustInput, head_trusted: bool) -> tuple[JobDecision, ...]:
    """GATE-010: per [gate.trust].skip job, from the BASE commit's
    declarations and the head's Ci-Attestation trailers."""

    if not inp.base_sha:
        return ()
    base = load_gate_config_at(repo, inp.base_sha).config
    if base is None or base.trust is None:
        return ()
    lanes = tuple(lane.id for lane in base.lanes)
    loaded = load_attestation_definition(repo, rev=inp.base_sha, lanes=lanes)
    definition = loaded.definition if loaded is not None else None
    ages = {lane.id: lane.max_age_hours for lane in base.lanes}
    commit = verify_attestations(repo, inp.head_sha, definition, max_age_hours=ages) if definition is not None else None
    decisions = decide_jobs(definition, base.trust.skip, head_trusted=head_trusted, commit=commit)
    if not any(item.skip for item in decisions):
        return decisions
    try:
        remote = non_attestable_jobs(load_workflows_at(repo, inp.base_sha))
    except (OSError, ValueError):
        return tuple(JobDecision(item.job, False, "base workflow provenance unavailable") for item in decisions)
    return tuple(JobDecision(item.job, False, "remote-only work remains required on GitHub")
                 if item.job in remote else item for item in decisions)

def trust_input(payload: dict[str, JsonValue], event: str, sha: str) -> TrustInput:
    pr = payload.get("pull_request")
    pr = pr if isinstance(pr, dict) else {}

    def text(obj: JsonValue, *keys: str) -> str | None:
        for key in keys:
            obj = obj.get(key) if isinstance(obj, dict) else None
        return obj if isinstance(obj, str) else None

    raw_labels = pr.get("labels")
    labels = tuple(
        label["name"] for label in (raw_labels if isinstance(raw_labels, list) else [])
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    )
    return TrustInput(
        event=event,
        head_sha=sha,
        base_sha=text(pr, "base", "sha"),
        author_association=text(pr, "author_association"),
        head_repo=text(pr, "head", "repo", "full_name"),
        base_repo=text(pr, "base", "repo", "full_name"),
        labels=labels,
    )


def verified_jobs(repo: Path, workflow: str, payload: dict[str, JsonValue], *, event: str,
                  needs: dict[str, JsonValue], local_replay: bool = False) -> tuple[JobDecision, ...]:
    """Re-decide the actual head; verifier outputs alone never authorize a skip."""
    if local_replay or event != "pull_request" or re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow) is None:
        return ()
    pr = payload.get("pull_request")
    head = pr.get("head") if isinstance(pr, dict) else None
    sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str):
        return ()
    inp = trust_input(payload, event, sha)
    if not inp.base_sha:
        return ()
    base = load_gate_config_at(repo, inp.base_sha).config
    if base is None or base.verify is None or base.verify.workflow != workflow:
        return ()
    verifier = needs.get(base.verify.job)
    if not isinstance(verifier, dict) or verifier.get("result") != "success":
        return ()
    outputs = verifier.get("outputs")
    if not isinstance(outputs, dict):
        return ()
    decision = decide(repo, inp)
    return tuple(replace(item, job=item.job.split(":", 1)[1])
                 for item in job_decisions(repo, inp, decision.trusted)
                 if item.skip and item.job.startswith(workflow + ":")
                 and outputs.get("skip_" + item.job.split(":", 1)[1]) == "true")
