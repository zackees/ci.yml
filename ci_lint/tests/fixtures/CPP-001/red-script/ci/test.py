import subprocess

subprocess.run(["ctest", "--test-dir", "build", "--output-on-failure"], check=True)
