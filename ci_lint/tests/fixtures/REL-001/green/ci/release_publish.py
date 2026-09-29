import os
import subprocess

_STRIP = ("CLICOLOR_FORCE", "FORCE_COLOR", "GH_FORCE_TTY")

def get_release_json(tag: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in _STRIP}
    env["NO_COLOR"] = "1"
    proc = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets,tagName"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout
