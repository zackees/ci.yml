"""Compile explicit public output declarations into the Bosn transport adapter."""

from ci_lint.workflow_replay_config import DeclaredReplayJob
from ci_lint.workflow_replay import ReplayJob
from ci_lint.workflow_replay_expansion import ExpandedJob
from ci_lint.workflow_scan import as_dict
from ci_lint.workflow_replay_outputs import ReplayOutput, valid_output_name


def capture_requests(declared: DeclaredReplayJob, expanded: ExpandedJob) -> tuple[str, ...]:
    if not declared.capture_outputs:
        return ()
    outputs = as_dict(as_dict(expanded.job).get("outputs"))
    if (not expanded.identity or any(not valid_output_name(part.job_id) for part in expanded.identity)
            or len(set(declared.capture_outputs)) != len(declared.capture_outputs)
            or any(not valid_output_name(name) or name not in outputs for name in declared.capture_outputs)):
        raise ValueError("requested public output is not declared by its qualified source job")
    path = "/".join(part.job_id for part in expanded.identity)
    return tuple(path + ":" + name for name in declared.capture_outputs)


def request_arguments(argv: tuple[str, ...], requests: tuple[str, ...]) -> tuple[str, ...]:
    if not requests:
        return argv
    if (len(requests) > 256 or any(len(item) > 1024 for item in requests)
            or argv[1:3] != ("ci", "run") or any(item.startswith("--ci-output") for item in argv)):
        raise ValueError("public output capture needs the Bosn ci run adapter and one declaration authority")
    return (*argv, *(arg for request in requests for arg in ("--ci-output", request)))


def require_captured_outputs(jobs: tuple[ReplayJob, ...], outputs: tuple[ReplayOutput, ...],
                             requests: tuple[str, ...]) -> None:
    for job in jobs:
        if job.excluded or job.remote_maintenance:
            continue
        path = "/".join(part.job_id for part in job.identity)
        names = tuple(request.split(":", 1)[1] for request in requests if request.startswith(path + ":"))
        if any(not any(output.identity == job.identity and output.name == name for output in outputs) for name in names):
            raise ValueError("executed producer lacks requested public output evidence")
