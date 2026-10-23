"""Isolated profile template selection tests using synthetic device serials."""
from pathlib import Path
import importlib.util
import os
import sys
import tempfile


def write_profile(path, serial, manufacturer, model, extra='ALTEMU_MAP_GB="example=>example-alt"'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"DEVICE_SERIAL={serial}\nDEVICE_MANUFACTURER={manufacturer!r}\n"
        f"DEVICE_MODEL={model!r}\nSYSTEMS=gb\n{extra}\n", encoding="utf-8")


def run():
    with tempfile.TemporaryDirectory(prefix="esde-gui-profile-") as temporary:
        root = Path(temporary)
        home = root / "home"
        home.mkdir()
        config = home / ".config"
        profiles = config / "esde-sync/profiles"
        rom_root = home / "Emulation/roms"
        for system in ("gb", "gbc", "arcade"):
            path = rom_root / system / "fixture.rom"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")

        template_rows = (
            ("SYNTH-CLASSIC", "Moorechip", "Retroid Pocket Classic", 'ALTEMU_MAP_GB="classic=>classic-alt"'),
            ("SYNTH-MINI", "Moorechip", "Retroid Pocket Mini V2", 'ALTEMU_MAP_GBC="mini=>mini-alt"'),
            ("SYNTH-AIR", "ARBOR", "GT78-VN", 'ALTEMU_MAP_ARCADE="air=>air-alt"'),
        )
        for serial, maker, model, extra in template_rows:
            write_profile(profiles / f"{serial}.conf", serial, maker, model, extra)

        os.environ.update({
            "HOME": str(home), "XDG_CONFIG_HOME": str(config),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "ESDE_SYNC_TEST_MODE": "1", "ESDE_SYNC_TEST_ROOT": str(root / "test-root"),
        })
        module_path = Path(__file__).resolve().parents[1] / "esde-sync-gui.py"
        sys.path.insert(0, str(module_path.parent))
        spec = importlib.util.spec_from_file_location("esde_gui_profile_fixture", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        for serial, maker, model, extra in template_rows:
            ok, _ = module.register_profile_from_template(serial + "-NEW", ["gb", "gbc"], maker, model)
            assert ok
            target = profiles / f"{serial}-NEW.conf"
            text = target.read_text(encoding="utf-8")
            assert f"DEVICE_SERIAL={serial}-NEW" in text
            assert module._profile_field(text, "DEVICE_MANUFACTURER") == maker
            assert module._profile_field(text, "DEVICE_MODEL") == model
            assert 'SYSTEMS="gb gbc"' in text
            assert extra in text

        unknown = "SYNTH-UNKNOWN-NEW"
        ok, _ = module.register_profile_from_template(unknown, ["arcade"], "Unknown Maker", "Unknown Model")
        assert ok
        fallback = (profiles / f"{unknown}.conf").read_text(encoding="utf-8")
        assert f"DEVICE_SERIAL={unknown}" in fallback
        assert 'SYSTEMS="arcade"' in fallback
        assert "ALTEMU_MAP_" not in fallback

        existing = profiles / "SYNTH-EXISTING.conf"
        original = ("DEVICE_SERIAL=SYNTH-EXISTING\nDEVICE_MANUFACTURER=Custom\n"
                    "DEVICE_MODEL=Existing\nSYSTEMS=gb\nCUSTOM_SETTING=keep\n")
        existing.write_text(original, encoding="utf-8")
        ok, reason = module.register_profile_from_template(
            "SYNTH-EXISTING", ["gbc"], "Moorechip", "Retroid Pocket Classic")
        assert ok and "이미 등록된" in reason
        assert existing.read_text(encoding="utf-8") == original

        # Serial remains the profile lookup key used by the engine and GUI.
        assert (profiles / "SYNTH-MINI-NEW.conf").is_file()
        assert module.PROFILE_DIR / "SYNTH-MINI-NEW.conf" == profiles / "SYNTH-MINI-NEW.conf"
        assert not any(path.name.endswith(".tmp") for path in profiles.iterdir())
        print("GUI profile template tests: PASS (Classic, Mini V2, Air Mini, fallback, existing profile, synthetic serial)")


if __name__ == "__main__":
    run()
