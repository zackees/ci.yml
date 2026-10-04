"""Validate Bosn's terminal workflow evidence before emitting local proof.

This module validates execution evidence. It does not establish that a
repository's declared jobs cover every remote check; static coverage is separate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ci_lint.cargo_messages import JsonValue


@dataclass(frozen=True)
class ReplayJob:
    key: str
    steps: tuple[str, ...]
    cache_save_steps: tuple[str, ...] = ()
    minimal_skip_steps: tuple[str, ...] = ()
    minimal_mode_step: str = ""


@dataclass(frozen=True)
class ReplayInput:
    name: str
    value: str


@dataclass(frozen=True)
class ReplayExpectation:
    repository: str
    workspace: Path
    sha: str
    git_tree: str
    workflow: str
    selected_job: str | None
    mode: str
    required_jobs: tuple[ReplayJob, ...]
    trigger: str = "pr"
    event: str = "pull_request"
    inputs: tuple[ReplayInput, ...] = ()


@dataclass(frozen=True)
class ReplayProof:
    jobs: tuple[str, ...]


def _inputs(raw: dict[str, JsonValue], expected: ReplayExpectation) -> None:
    if len({item.name for item in expected.inputs}) != len(expected.inputs):
        raise ValueError("workflow replay input expectations are duplicated")
    if any(not item.name.strip() for item in expected.inputs):
        raise ValueError("workflow replay input name is empty")
    params = raw.get("params")
    actual = params.get("inputs") if isinstance(params, dict) else None
    wanted = {item.name: item.value for item in expected.inputs}
    if actual is None and not wanted:
        return
    if not isinstance(actual, dict) or actual != wanted:
        raise ValueError("workflow replay inputs differ from the declared selection")


def _metadata(raw: dict[str, JsonValue], expected: ReplayExpectation) -> None:
    scalars: dict[str, str | int | None] = {
        "schema_version": 1, "provider": "github", "repository": expected.repository, "engine": "act",
        "sha": expected.sha, "git_tree": expected.git_tree, "dirty": None,
        "workflow": expected.workflow, "job": expected.selected_job,
        "trigger": expected.trigger, "event": expected.event, "mode": expected.mode, "state": "done",
        "conclusion": "success", "exit_code": 0, "act_exit_code": 0,
        "cleanup": "removed",
    }
    for key, value in scalars.items():
        if key not in raw or type(raw[key]) is not type(value) or raw[key] != value:
            raise ValueError(f"workflow replay metadata mismatch: {key}")
    workspace = raw.get("workspace")
    if not isinstance(workspace, str) or Path(workspace).resolve() != expected.workspace.resolve():
        raise ValueError("workflow replay ran another workspace")
    digest = raw.get("tree_digest")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("workflow replay has no source snapshot digest")
    version = raw.get("act_version")
    match = re.fullmatch(r"\d+\.\d+\.\d+-act2\.(\d+)", version) if isinstance(version, str) else None
    if match is None or int(match.group(1)) < 3:
        raise ValueError("workflow replay requires act2.3 or later executed-step evidence")


@dataclass(frozen=True)
class _JobDocument:
    key: str
    raw: dict[str, JsonValue]


def _jobs(raw: JsonValue) -> tuple[_JobDocument, ...]:
    # These maps are parsed JSON documents; callers receive validated proof records.
    if not isinstance(raw, dict) or type(raw.get("malformed_lines")) is not int or raw["malformed_lines"] != 0:
        raise ValueError("workflow replay job tree is malformed")
    groups = raw.get("groups")
    if not isinstance(groups, list):
        raise ValueError("workflow replay groups are missing")
    jobs: dict[str, _JobDocument] = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("jobs"), list):
            raise ValueError("workflow replay group is malformed")
        for job in group["jobs"]:
            if not isinstance(job, dict):
                raise ValueError("workflow replay job is malformed")
            key = job.get("key")
            if not isinstance(key, str) or not key or key in jobs:
                raise ValueError("workflow replay job identity is missing or duplicated")
            jobs[key] = _JobDocument(key, job)
    return tuple(jobs.values())


def _step(section: JsonValue, name: str) -> bool:
    if not isinstance(section, dict) or section.get("name") != name:
        return False
    first = section.get("first_seq")
    last = section.get("last_seq")
    return (section.get("stage") == "Main" and section.get("status") == "completed"
            and section.get("conclusion") == "success" and type(first) is int and type(last) is int
            and 0 < first <= last)


def _prove_job(raw: dict[str, JsonValue], expected: ReplayJob) -> None:
    if raw.get("status") != "completed" or raw.get("conclusion") != "success":
        raise ValueError(f"workflow replay job did not pass: {expected.key}")
    if (len(set(expected.cache_save_steps)) != len(expected.cache_save_steps)
            or not set(expected.cache_save_steps).issubset(expected.steps)):
        raise ValueError("cache-save steps must be distinct declared steps")
    sections = raw.get("sections")
    if not isinstance(sections, list):
        raise ValueError(f"workflow replay job has no executed steps: {expected.key}")
    for name in expected.steps:
        named = [section for section in sections if isinstance(section, dict) and section.get("name") == name
                 and section.get("stage") == "Main"]
        skipped_save = (name in expected.cache_save_steps and len(named) == 1
                        and named[0].get("status") == "completed" and named[0].get("conclusion") == "skipped")
        skipped_profile = (len(named) == 1 and named[0].get("status") == "completed"
                           and named[0].get("conclusion") == "skipped")
        accepted = skipped_profile if name in expected.minimal_skip_steps else (_step(named[0], name) if len(named) == 1 else False) or skipped_save
        if len(named) != 1 or not accepted:
            raise ValueError(f"workflow replay lacks an unambiguous executed check: {expected.key}: {name}")


def _minimal_skips(expected: ReplayExpectation) -> None:
    for job in expected.required_jobs:
        skips = set(job.minimal_skip_steps)
        if not skips:
            if job.minimal_mode_step:
                raise ValueError("minimal mode producer requires declared exclusions")
            continue
        if (expected.mode != "minimal" or expected.event != "pull_request"
                or len(skips) != len(job.minimal_skip_steps) or not skips.issubset(job.steps)
                or skips.intersection(job.cache_save_steps)
                or not job.minimal_mode_step or job.minimal_mode_step not in job.steps
                or job.minimal_mode_step in skips or job.minimal_mode_step in job.cache_save_steps):
            raise ValueError("minimal exclusions require a mandatory producer and a minimal PR selection")


def prove_replay(raw: JsonValue, expected: ReplayExpectation) -> ReplayProof:
    if not isinstance(raw, dict):
        raise ValueError("workflow replay evidence must be a JSON object")
    if not expected.required_jobs or len({job.key for job in expected.required_jobs}) != len(expected.required_jobs):
        raise ValueError("workflow replay requires distinct named jobs")
    _minimal_skips(expected)
    if any(not job.key or not job.steps or len(set(job.steps)) != len(job.steps) for job in expected.required_jobs):
        raise ValueError("workflow replay requires distinct executed checks in each job")
    if re.fullmatch(r"[0-9a-f]{40}", expected.sha) is None or re.fullmatch(r"[0-9a-f]{40}", expected.git_tree) is None:
        raise ValueError("workflow replay requires exact source and tree object IDs")
    if (expected.trigger, expected.event) not in (("pr", "pull_request"), ("workflow_dispatch", "workflow_dispatch")):
        raise ValueError("workflow replay requires a declared PR or dispatch selection")
    _inputs(raw, expected)
    _metadata(raw, expected)
    jobs = {item.key: item for item in _jobs(raw.get("tree"))}
    for job in expected.required_jobs:
        if job.key not in jobs:
            raise ValueError(f"workflow replay required job is missing: {job.key}")
        _prove_job(jobs[job.key].raw, job)
    return ReplayProof(tuple(job.key for job in expected.required_jobs))
