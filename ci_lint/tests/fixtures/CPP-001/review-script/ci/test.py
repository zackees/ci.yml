import subprocess
import sys

build_dir = sys.argv[1]
cmd = ["ctest", "--test-dir", build_dir]
subprocess.run(cmd, check=True)
