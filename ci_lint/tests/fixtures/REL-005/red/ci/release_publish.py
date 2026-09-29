import subprocess


def publish_candidate(sha: str) -> None:
    # Rebuilds the wheel on the publish path instead of downloading the
    # already-validated candidate's build artifacts by run id -- REL-005.
    subprocess.run(["cargo", "build", "--release"], check=True)
    subprocess.run(["uv", "build", "--wheel"], check=True)
