import subprocess

subprocess.run(
    ["soldr", "cargo", "test", "--locked", "--manifest-path", "lints/foo/Cargo.toml"],
    check=True,
)
