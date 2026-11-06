import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
OTA_DIR = REPO / "firmware" / "ota"
sys.path.insert(0, str(TOOLS))

import sentinel_sign as ss  # noqa: E402


@pytest.fixture(scope="session")
def key():
    from cryptography.hazmat.primitives.asymmetric import ec

    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="session")
def keyfiles(tmp_path_factory, key):
    """Key pair on disk via the CLI (exercises keygen)."""
    d = tmp_path_factory.mktemp("keys")
    assert ss.main(["keygen", "--out-dir", str(d), "--name", "t"]) == 0
    return {
        "priv": d / "t.key.pem",
        "pub_pem": d / "t.pub.pem",
        "pub_bin": d / "t.pub.bin",
        "pub_h": d / "t.pub.h",
    }


@pytest.fixture(scope="session")
def host_verify():
    """Path to the C ota_host_verify binary. Uses $OTA_HOST_VERIFY, else builds
    firmware/ota into firmware/ota/build with CMake."""
    env = os.environ.get("OTA_HOST_VERIFY")
    if env:
        p = Path(env)
        assert p.is_file(), f"OTA_HOST_VERIFY={env} does not exist"
        return p
    build = OTA_DIR / "build"
    subprocess.run(["cmake", "-S", str(OTA_DIR), "-B", str(build)], check=True, capture_output=True)
    subprocess.run(["cmake", "--build", str(build), "--target", "ota_host_verify", "-j"], check=True,
                   capture_output=True)
    return build / "ota_host_verify"
