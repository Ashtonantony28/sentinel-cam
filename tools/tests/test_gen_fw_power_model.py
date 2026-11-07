"""tools/gen_fw_power_model.py: firmware power model generated from hardware constants.yaml."""
import re
import sys

import pytest

from conftest import REPO, TOOLS

sys.path.insert(0, str(TOOLS))
import gen_fw_power_model as gen  # noqa: E402


def test_checked_in_generated_file_is_fresh():
    """Fails when constants.yaml / scenarios.yaml / the generator changed without regenerating."""
    assert gen.OUT.exists(), "run tools/gen_fw_power_model.py"
    assert gen.OUT.read_text() == gen.render(), (
        f"{gen.OUT.relative_to(REPO)} is stale: run `python3 tools/gen_fw_power_model.py`")
    assert gen.main(["--check"]) == 0


def test_header_and_values_match_derivation():
    text = gen.OUT.read_text()
    assert "GENERATED FILE - DO NOT EDIT" in text
    assert gen.source_hash() in text
    d = gen.derive()
    for k in gen.FIELDS:
        m = re.search(rf"\.{k}\s*=\s*(\d+)u,", text)
        assert m, k
        assert int(m.group(1)) == d[k][0]


def test_mapping_uses_hardware_model():
    sp = gen._sentinel_power()
    const = sp.load_constants()
    p = const.nominal()
    scen = sp.load_scenarios()[gen.STANDBY_SCENARIO]
    d = gen.derive()
    # standby is the hardware model's battery-side TWT standby: I_bat x V_bat
    i_bat = float(sp.model.standby_current_at_battery(p, scen))
    assert d["standby_uw"][1] == pytest.approx(i_bat * p["battery.nominal_v"] * 1e6)
    assert d["cortex_boot_mw"][0] == round(p["cortex.boot_power"] * 1e3)
    assert d["cortex_active_mw"][0] == round(p["cortex.pipeline_power"] * 1e3)
    assert d["cortex_clip_mw"][0] == round(p["cortex.encode_power"] * 1e3)
    assert d["boot_est_mj"][1] == pytest.approx(p["cortex.boot_power"] * p["cortex.boot_time"] * 1e3)
    assert d["uplink_est_mj"][1] == pytest.approx(p["cortex.pipeline_power"] * p["radio.publish_time"] * 1e3)


def test_check_detects_a_changed_constant(tmp_path):
    src = gen.CONSTANTS.read_text()
    changed = src.replace("boot_power:          {value: 0.35,", "boot_power:          {value: 0.45,")
    assert changed != src
    c = tmp_path / "constants.yaml"
    c.write_text(changed)
    out = tmp_path / "gen.c"
    assert gen.main(["--constants", str(c), "--out", str(out)]) == 0
    assert ".cortex_boot_mw   = 450u," in out.read_text()
    # the checked-in file no longer matches the changed source
    assert gen.main(["--constants", str(c), "--check"]) == 1
    assert gen.main(["--constants", str(c), "--out", str(out), "--check"]) == 0


@pytest.mark.parametrize("x,want", [(0.5, 1), (1.5, 2), (2.5, 3), (127.5, 128), (52.49, 52), (-0.5, -1)])
def test_round_half_away(x, want):
    assert gen.round_half_away(x) == want
