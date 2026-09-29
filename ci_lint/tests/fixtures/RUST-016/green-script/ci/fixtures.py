import subprocess

subprocess.run(
    ["soldr", "cargo", "test", "--locked", "--target-dir", "/tmp/fixtures"],
    cwd="lints/foo",
    check=True,
)
