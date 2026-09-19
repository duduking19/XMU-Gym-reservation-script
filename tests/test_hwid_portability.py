import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Non-Windows startup regression")
def test_hwid_without_windows_registry():
    script = """
import sys
sys.modules['winreg'] = None
from xdty_booking.security.hwid import get_hardware_info, get_hwid
assert get_hardware_info()['motherboard_uuid'] != 'UNKNOWN_MB'
first = get_hwid()
assert first.startswith('XMU-') and len(first) == 23
assert get_hwid() == first
from xdty_booking.security.auth import check_license
assert check_license(force_refresh=True).is_dev
print(first)
"""
    env = dict(os.environ)
    env.pop("FORCE_LICENSE_CHECK", None)
    results = [subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        timeout=15, env=env,
    ) for _ in range(2)]
    for result in results:
        assert result.returncode == 0, result.stderr
    assert results[0].stdout == results[1].stdout
