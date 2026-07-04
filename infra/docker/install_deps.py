"""Install all project dependencies from pyproject.toml without building the package."""

import tomllib
import subprocess
import sys

with open("pyproject.toml", "rb") as f:
    data = tomllib.load(f)

deps = data["project"]["dependencies"]
dev = data["project"]["optional-dependencies"]["dev"]

subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir"] + deps + dev)
