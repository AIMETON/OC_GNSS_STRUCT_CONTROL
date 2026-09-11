from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/plain,application/xml,text/xml,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "close",
}


@dataclass(frozen=True)
class ReviewedHttpResponse:
    raw: bytes
    content_type: str
    transport: str


def _curl_fetch(url: str, *, connect_timeout_s: float, transfer_timeout_s: float) -> ReviewedHttpResponse:
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if curl is None:
        raise OSError("curl/curl.exe is unavailable")

    scheme = urlsplit(url).scheme.lower()
    command = [
        curl,
        "--fail-with-body",
        "--location",
        "--silent",
        "--show-error",
        "--connect-timeout",
        str(max(1, int(connect_timeout_s))),
        "--max-time",
        str(max(2, int(transfer_timeout_s))),
    ]
    if scheme == "ftp":
        # Field-qualified path: anonymous passive FTP to Wuhan IGS works on target Windows.
        # Keep transfer timeout much larger than connect timeout: the archive can be slow
        # while remaining healthy, and a 1-2 MiB BRDC file may need several minutes.
        command.extend(["--ftp-pasv", "--disable-epsv"])
    else:
        command.extend(
            [
                "--user-agent",
                _BROWSER_HEADERS["User-Agent"],
                "--header",
                "Accept: text/plain,application/xml,text/xml,text/html;q=0.9,*/*;q=0.8",
            ]
        )
    command.append(url)

    try:
        completed = subprocess.run(  # noqa: S603 - executable and URL are caller-reviewed, shell=False
            command,
            check=False,
            capture_output=True,
            timeout=transfer_timeout_s + 15.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError(f"curl transport failed: {exc}") from exc

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise OSError(f"curl_exit={completed.returncode}; curl={stderr or 'no stderr'}")
    return ReviewedHttpResponse(raw=completed.stdout, content_type="", transport=f"curl-{scheme or 'url'}")


def fetch_reviewed_url(
    url: str,
    *,
    timeout_s: float = 20.0,
    transfer_timeout_s: float | None = None,
) -> ReviewedHttpResponse:
    """Fetch a caller-reviewed URL through protocol-aware Windows-friendly transports.

    ``timeout_s`` is the connection/read timeout for the primary attempt. Large/slow FTP
    payloads receive a substantially longer transfer budget so a healthy Wuhan download
    is not rejected merely because throughput is low. Callers remain responsible for
    strict host/path allowlisting and payload validation.
    """

    if timeout_s <= 0.0:
        raise ValueError("timeout_s must be positive")
    scheme = urlsplit(url).scheme.lower()
    if scheme not in {"http", "https", "ftp"}:
        raise ValueError(f"unsupported reviewed URL scheme: {scheme or '<missing>'}")

    if transfer_timeout_s is None:
        transfer_timeout_s = max(timeout_s + 5.0, 300.0 if scheme == "ftp" else timeout_s + 5.0)
    if transfer_timeout_s <= 0.0:
        raise ValueError("transfer_timeout_s must be positive")

    # For FTP prefer the exact transport already field-qualified on target Windows:
    # system curl/curl.exe in passive mode. urllib remains a fallback only.
    if scheme == "ftp":
        curl_error: Exception | None = None
        try:
            return _curl_fetch(
                url,
                connect_timeout_s=timeout_s,
                transfer_timeout_s=transfer_timeout_s,
            )
        except OSError as exc:
            curl_error = exc

        urllib_error: Exception | None = None
        try:
            with urlopen(url, timeout=max(timeout_s, 60.0)) as response:  # noqa: S310 - caller allowlists URL
                return ReviewedHttpResponse(
                    raw=response.read(),
                    content_type=response.headers.get("Content-Type", ""),
                    transport="urllib-ftp",
                )
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            urllib_error = exc
        raise OSError(
            "FTP fetch failed via both transports; "
            f"curl={curl_error}; urllib={urllib_error}"
        ) from urllib_error

    request = Request(url, headers=_BROWSER_HEADERS)
    urllib_error: Exception | None = None
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - caller performs allowlist validation
            return ReviewedHttpResponse(
                raw=response.read(),
                content_type=response.headers.get("Content-Type", ""),
                transport="urllib-http",
            )
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        urllib_error = exc

    try:
        return _curl_fetch(
            url,
            connect_timeout_s=timeout_s,
            transfer_timeout_s=transfer_timeout_s,
        )
    except OSError as curl_error:
        raise OSError(
            "HTTP fetch failed via both transports; "
            f"urllib={urllib_error}; curl={curl_error}"
        ) from urllib_error
