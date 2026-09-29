import subprocess

def get_release_json(tag: str) -> str:
    proc = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets,tagName"],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout
