import os
import subprocess

cmd = ["soldr", "cargo", "nextest", "run", "--locked"]
if os.environ.get("TEST_NOCAPTURE") == "1":
    cmd.append("--no-capture")
subprocess.run(cmd, check=True)
