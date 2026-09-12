from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from math import pi, radians, sqrt
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from constellation_control.adapters.reviewed_http_fetch import fetch_reviewed_url

GSC_ALMANAC_INDEX_URL = "https://www.gsc-europa.eu/gsc-products/almanac"
GSC_FILE_PREFIX = "https://www.gsc-europa.eu/sites/default/files/"
GSC_DAILY_FILE_PREFIX = "https://www.gsc-europa.eu/sites/default/files/sites/all/files/"
GALILEO_NOMINAL_SEMI_MAJOR_AXIS_M = 29_600_000.0
GALILEO_REFERENCE_INCLINATION_RAD = radians(56.0)

_REQUIRED_FIELDS = (
    "SVID",
    "aSqRoot",
    "ecc",
    "deltai",
    "omega0",
    "omegaDot",
    "w",
    "m0",
    "af0",
    "af1",
    "iod",
    "t0a",
    "wna",
    "statusE5a",
    "statusE5b",
    "statusE1B",
)


@dataclass(frozen=True)
class GalileoGscAlmanacRecord:
    svid: int
    delta_sqrt_a_m_sqrt: float
    eccentricity: float
    delta_inclination_semicircles: float
    raan_semicircles: float
    raan_rate_semicircles_s: float
    argument_of_perigee_semicircles: float
    mean_anomaly_semicircles: float
    af0_s: float
    af1_s_s: float
    iod: int
    t0a_s: float
    wna_mod4: int
    status_e5a: int
    status_e5b: int
    status_e1b: int

    @property
    def sqrt_a_m_sqrt(self) -> float:
        return sqrt(GALILEO_NOMINAL_SEMI_MAJOR_AXIS_M) + self.delta_sqrt_a_m_sqrt

    @property
    def semi_major_axis_m(self) -> float:
        return self.sqrt_a_m_sqrt**2

    @property
    def inclination_rad(self) -> float:
        return GALILEO_REFERENCE_INCLINATION_RAD + self.delta_inclination_semicircles * pi

    @property
    def raan_rad(self) -> float:
        return self.raan_semicircles * pi

    @property
    def raan_rate_rad_s(self) -> float:
        return self.raan_rate_semicircles_s * pi

    @property
    def argument_of_perigee_rad(self) -> float:
        return self.argument_of_perigee_semicircles * pi

    @property
    def mean_anomaly_rad(self) -> float:
        return self.mean_anomaly_semicircles * pi


@dataclass(frozen=True)
class GalileoGscAlmanac:
    source_url: str | None
    source_filename: str
    source_sha256: str
    records: tuple[GalileoGscAlmanacRecord, ...]
    authority_note: str = (
        "Official European GNSS Service Centre Galileo almanac XML; OS SIS ICD almanac semantics are preserved explicitly"
    )


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _number(value: str, label: str) -> float:
    try:
        return float(value.strip().replace("D", "E").replace("d", "e"))
    except ValueError as exc:
        raise ValueError(f"invalid Galileo GSC {label}: {value!r}") from exc


def _integer(value: str, label: str) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"invalid Galileo GSC {label}: {value!r}") from exc


def _candidate_sort_key(url: str) -> tuple[int, str]:
    name = urlparse(url).path.rsplit("/", 1)[-1]
    daily = re.search(r"GalileoGSCAlmanac_(\d{14})_.*\.xml$", name, re.IGNORECASE)
    if daily:
        return 2, daily.group(1)
    legacy = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})\.xml", name, re.IGNORECASE)
    if legacy:
        return 1, "".join(legacy.groups()) + "000000"
    return 0, ""


def gsc_daily_almanac_url(day: date) -> str:
    return f"{GSC_DAILY_FILE_PREFIX}{day.isoformat()}.xml"


def discover_latest_gsc_almanac_url(index_html: str) -> str:
    parser = _LinkParser()
    parser.feed(index_html)
    candidates: list[str] = []
    for href in parser.hrefs:
        absolute = urljoin(GSC_ALMANAC_INDEX_URL, href)
        parsed = urlparse(absolute)
        if parsed.scheme != "https" or parsed.netloc != "www.gsc-europa.eu":
            continue
        if not absolute.startswith(GSC_FILE_PREFIX):
            continue
        if not parsed.path.lower().endswith(".xml"):
            continue
        if _candidate_sort_key(absolute)[0] == 0:
            continue
        candidates.append(absolute)
    if not candidates:
        raise ValueError(
            "GSC almanac index contains no supported Galileo XML link under the official /sites/default/files/ tree"
        )
    return max(candidates, key=_candidate_sort_key)


def _record_fields(element: ElementTree.Element) -> dict[str, str] | None:
    # GSC has used both flat SV records and records where the orbital/status
    # parameters are grouped below nested child elements. SVID remains the
    # record discriminator, so only subtrees with a direct SVID child are
    # candidates; required values may then be descendants of that same record.
    direct = {_local_name(child.tag): (child.text or "").strip() for child in list(element)}
    if "SVID" not in direct:
        return None

    fields: dict[str, str] = {}
    for node in element.iter():
        if node is element:
            continue
        name = _local_name(node.tag)
        if name not in _REQUIRED_FIELDS:
            continue
        value = (node.text or "").strip()
        if not value:
            continue
        existing = fields.get(name)
        if existing is not None and existing != value:
            raise ValueError(f"Galileo GSC record contains conflicting {name} values")
        fields[name] = value
    return fields


def parse_galileo_gsc_almanac(
    filename: str,
    xml_text: str,
    *,
    source_url: str | None = None,
) -> GalileoGscAlmanac:
    if not filename or "/" in filename or "\\" in filename:
        raise ValueError("Galileo GSC source filename must not contain path components")
    if not filename.lower().endswith(".xml"):
        raise ValueError("Galileo GSC source filename must end with .xml")
    if not xml_text.strip():
        raise ValueError("Galileo GSC XML is empty")
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ValueError("Galileo GSC XML is invalid") from exc

    records: list[GalileoGscAlmanacRecord] = []
    for element in root.iter():
        fields = _record_fields(element)
        if fields is None:
            continue
        missing = [field for field in _REQUIRED_FIELDS if field not in fields]
        if missing:
            raise ValueError("Galileo GSC record missing fields: " + ", ".join(missing))
        record = GalileoGscAlmanacRecord(
            svid=_integer(fields["SVID"], "SVID"),
            delta_sqrt_a_m_sqrt=_number(fields["aSqRoot"], "aSqRoot"),
            eccentricity=_number(fields["ecc"], "ecc"),
            delta_inclination_semicircles=_number(fields["deltai"], "deltai"),
            raan_semicircles=_number(fields["omega0"], "omega0"),
            raan_rate_semicircles_s=_number(fields["omegaDot"], "omegaDot"),
            argument_of_perigee_semicircles=_number(fields["w"], "w"),
            mean_anomaly_semicircles=_number(fields["m0"], "m0"),
            af0_s=_number(fields["af0"], "af0"),
            af1_s_s=_number(fields["af1"], "af1"),
            iod=_integer(fields["iod"], "iod"),
            t0a_s=_number(fields["t0a"], "t0a"),
            wna_mod4=_integer(fields["wna"], "wna"),
            status_e5a=_integer(fields["statusE5a"], "statusE5a"),
            status_e5b=_integer(fields["statusE5b"], "statusE5b"),
            status_e1b=_integer(fields["statusE1B"], "statusE1B"),
        )
        if not 1 <= record.svid <= 36:
            raise ValueError(f"Galileo GSC SVID out of range: {record.svid}")
        if not 0.0 <= record.eccentricity < 1.0:
            raise ValueError(f"Galileo GSC eccentricity out of range for SVID {record.svid}")
        if record.sqrt_a_m_sqrt <= 0.0:
            raise ValueError(f"Galileo GSC sqrt(A) is non-positive for SVID {record.svid}")
        if record.t0a_s < 0.0:
            raise ValueError(f"Galileo GSC t0a is negative for SVID {record.svid}")
        if not 0 <= record.wna_mod4 <= 3:
            raise ValueError(f"Galileo GSC WNa modulo-4 is out of range for SVID {record.svid}")
        records.append(record)

    if not records:
        raise ValueError("Galileo GSC XML contains no almanac records")
    svids = [record.svid for record in records]
    if len(svids) != len(set(svids)):
        raise ValueError("duplicate Galileo GSC SVID values")

    return GalileoGscAlmanac(
        source_url=source_url,
        source_filename=filename,
        source_sha256=hashlib.sha256(xml_text.encode("utf-8")).hexdigest(),
        records=tuple(records),
    )


def _fetch_text(url: str, timeout_s: float) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "www.gsc-europa.eu":
        raise ValueError(f"Galileo GSC URL is outside the reviewed host allowlist: {url}")
    if url != GSC_ALMANAC_INDEX_URL and not url.startswith(GSC_FILE_PREFIX):
        raise ValueError(f"Galileo GSC file URL is outside the reviewed file tree: {url}")
    try:
        response = fetch_reviewed_url(url, timeout_s=timeout_s)
    except OSError as exc:
        raise ValueError(f"Galileo GSC online source unavailable: {url}: {exc}") from exc
    raw = response.raw
    if not raw:
        raise ValueError(f"Galileo GSC response is empty: {url} (transport={response.transport})")
    content_type = response.content_type.lower()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8-sig")
    if url != GSC_ALMANAC_INDEX_URL and ("html" in content_type or "<html" in text[:256].lower()):
        raise ValueError(f"Galileo GSC XML URL returned HTML (transport={response.transport}): {url}")
    return text


def fetch_galileo_gsc_almanac_for_date(day: date, *, timeout_s: float = 20.0) -> GalileoGscAlmanac:
    xml_url = gsc_daily_almanac_url(day)
    xml_text = _fetch_text(xml_url, timeout_s)
    filename = urlparse(xml_url).path.rsplit("/", 1)[-1]
    return parse_galileo_gsc_almanac(filename, xml_text, source_url=xml_url)


def fetch_latest_galileo_gsc_almanac(
    *,
    timeout_s: float = 20.0,
    as_of: date | None = None,
    direct_lookback_days: int = 14,
) -> GalileoGscAlmanac:
    if direct_lookback_days < 0:
        raise ValueError("direct_lookback_days must be non-negative")
    anchor = as_of or datetime.now(UTC).date()
    direct_errors: list[str] = []
    for offset in range(direct_lookback_days + 1):
        candidate_day = anchor - timedelta(days=offset)
        try:
            return fetch_galileo_gsc_almanac_for_date(candidate_day, timeout_s=timeout_s)
        except ValueError as exc:
            direct_errors.append(f"{candidate_day.isoformat()}: {exc}")

    try:
        index_html = _fetch_text(GSC_ALMANAC_INDEX_URL, timeout_s)
        xml_url = discover_latest_gsc_almanac_url(index_html)
        xml_text = _fetch_text(xml_url, timeout_s)
        filename = urlparse(xml_url).path.rsplit("/", 1)[-1]
        return parse_galileo_gsc_almanac(filename, xml_text, source_url=xml_url)
    except ValueError as exc:
        detail = "; ".join(direct_errors[-4:])
        raise ValueError(
            "Galileo GSC direct daily XML and product index are unavailable; "
            f"recent_direct_attempts=[{detail}]; index={exc}"
        ) from exc
