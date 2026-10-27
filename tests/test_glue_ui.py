"""No browser service or microphone is needed to verify voice interaction ordering."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_handsfree_interactions_without_network():
    node = shutil.which('node')
    if not node:
        pytest.skip('Optional Node UI event verification; Python/API coverage still runs.')
    script = Path(__file__).with_name('glue_ui.mjs')
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
