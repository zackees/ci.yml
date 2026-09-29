import subprocess

subprocess.run(
    ["soldr", "cargo", "nextest", "run", "--locked", "--features=daemon", "--tests", "-E", "test(daemon)"],
    check=True,
)
