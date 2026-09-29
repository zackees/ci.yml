import subprocess

subprocess.run(["soldr", "cargo", "nextest", "run", "--locked", "--nocapture"], check=True)
