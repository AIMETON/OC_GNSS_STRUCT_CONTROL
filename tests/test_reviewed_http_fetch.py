from __future__ import annotations

from types import SimpleNamespace

from constellation_control.adapters.reviewed_http_fetch import fetch_reviewed_url


def test_ftp_prefers_field_qualified_curl_and_uses_long_transfer_budget(monkeypatch) -> None:
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        "constellation_control.adapters.reviewed_http_fetch.shutil.which",
        lambda name: "C:/Windows/System32/curl.exe" if name in {"curl", "curl.exe"} else None,
    )

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["timeout"] = kwargs["timeout"]
        return SimpleNamespace(returncode=0, stdout=b"ftp-payload", stderr=b"")

    monkeypatch.setattr(
        "constellation_control.adapters.reviewed_http_fetch.subprocess.run",
        fake_run,
    )

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("FTP should use the field-qualified curl path first")

    monkeypatch.setattr(
        "constellation_control.adapters.reviewed_http_fetch.urlopen",
        fail_urlopen,
    )

    response = fetch_reviewed_url("ftp://igs.gnsswhu.cn/pub/gps/data/daily/x.rnx.gz", timeout_s=30.0)

    assert response.raw == b"ftp-payload"
    assert response.transport == "curl-ftp"
    command = seen["command"]
    assert "--ftp-pasv" in command
    assert "--max-time" in command
    max_time = int(command[command.index("--max-time") + 1])
    assert max_time >= 300
    assert float(seen["timeout"]) >= 315.0


def test_https_keeps_short_default_transfer_budget(monkeypatch) -> None:
    class BrokenResponse:
        def __enter__(self):
            raise OSError("reset")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        "constellation_control.adapters.reviewed_http_fetch.urlopen",
        lambda *_args, **_kwargs: BrokenResponse(),
    )
    monkeypatch.setattr(
        "constellation_control.adapters.reviewed_http_fetch.shutil.which",
        lambda _name: "curl.exe",
    )
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(
        "constellation_control.adapters.reviewed_http_fetch.subprocess.run",
        fake_run,
    )

    response = fetch_reviewed_url("https://example.invalid/data", timeout_s=20.0)
    assert response.transport == "curl-https"
    command = seen["command"]
    max_time = int(command[command.index("--max-time") + 1])
    assert max_time == 25
