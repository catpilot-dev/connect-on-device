# tests/test_clips_store.py
import os

def test_config_exposes_clip_paths():
    import config
    assert config.COD_CLIPS_DIR == os.getenv("COD_CLIPS_DIR", "/data/media/0/cod_clips")
    assert config.ENCODERD_BIN.endswith("openpilot/system/loggerd/encoderd")

def test_version_is_0_11_2():
    from pathlib import Path
    assert Path(__file__).resolve().parent.parent.joinpath("VERSION").read_text().strip() == "0.11.2"
