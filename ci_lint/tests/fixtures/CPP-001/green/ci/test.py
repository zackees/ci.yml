import os
import subprocess

env = dict(os.environ, CTEST_PARALLEL_LEVEL=str(os.cpu_count()))
subprocess.run(["ctest", "--test-dir", "build"], env=env, check=True)
