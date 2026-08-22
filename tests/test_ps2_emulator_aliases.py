from app.config import romm_emulator_for


def test_ps2_android_emulator_aliases_map_to_pcsx2():
    for alias in (
        "aethersx2",
        "nethersx2",
        "aethersx2-turnip",
        "nethersx2-turnip",
        "xyz.aethersx2.android",
        "xyz.nethersx2.android",
        "xyz.aethersx2.custom",
        "xyz.aethersx2.tturnip",
    ):
        assert romm_emulator_for(alias) == "pcsx2"
