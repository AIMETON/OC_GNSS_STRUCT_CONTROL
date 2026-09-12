from pathlib import Path

from constellation_control.preview import source_runtime


def test_unix_compress_decoder_accepts_unlzw3_bytes(monkeypatch) -> None:
    expected = b"     2.11           GLONASS NAV DATA                         RINEX VERSION / TYPE\n"
    monkeypatch.setattr(source_runtime.unlzw3, "unlzw", lambda payload: expected)
    assert source_runtime._decode_rinex_payload(b"\x1f\x9dplaceholder", "zeck2540.26g.Z") == expected


def test_preview_runtime_lock_packages_unlzw3() -> None:
    lock = Path("preview/requirements-preview.lock").read_text(encoding="utf-8").splitlines()
    assert "unlzw3==0.2.3" in lock
