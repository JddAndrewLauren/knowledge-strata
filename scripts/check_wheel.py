"""Install a built wheel in a fresh environment and verify packaged startup.

Run after `python -m build --wheel`; dependencies come from the package index.
This is installation evidence, not a host or model-download acceptance test.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

wheel = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='strata-wheel-') as temporary:
    root = Path(temporary)
    venv.create(root, with_pip=True)
    python = root / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    subprocess.run([str(python), '-m', 'pip', 'install', str(wheel)], check=True)
    subprocess.run([str(python), '-m', 'strata', '--help'], cwd=root, check=True)
    subprocess.run([str(python), '-c',
                    "from importlib.resources import files; "
                    "assert files('strata').joinpath('resources/SKILL.md').is_file(); "
                    "assert files('strata').joinpath('resources/strata-reader.md').is_file(); "
                    "from strata.server import create_server"], cwd=root, check=True)
