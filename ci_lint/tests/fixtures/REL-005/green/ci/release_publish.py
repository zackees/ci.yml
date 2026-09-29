import subprocess


def publish_candidate(run_id: str) -> None:
    # Downloads the already-validated candidate's build artifacts by run id
    # instead of rebuilding them.
    subprocess.run(
        ["gh", "run", "download", run_id, "--dir", "dist", "--pattern", "wheel-*"],
        check=True,
    )
