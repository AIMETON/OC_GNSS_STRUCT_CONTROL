from __future__ import annotations

from pathlib import Path

from constellation_control.adapters.gnss_almanac import GnssAlmanacFormat
from constellation_control.adapters.orekit.mean_conversion import MeanConversionResult
from constellation_control.application.run import load_scenario
from constellation_control.domain.models import MeanElementDefinition, MeanOrbit
from constellation_control.preview import gnss_almanac_input
from constellation_control.preview.gnss_almanac_input import (
    GNSS_ALMANAC_CARD,
    GNSS_ALMANAC_SCRIPT,
    GpsAlmanacCreateRequest,
    create_gps_almanac_derived_scenario,
)

YUMA_TWO = """******** Week 388 almanac for PRN-01 ********
ID:                         1
Health:                     000
Eccentricity:               0.001
Time of Applicability(s):  147456.0
Orbital Inclination(rad):   0.95
Rate of Right Ascen(r/s):  -0.000000008
SQRT(A)  (m 1/2):           5153.6
Right Ascen at Week(rad):   0.1
Argument of Perigee(rad):   0.2
Mean Anom(rad):             0.3
Af0(s):                     0.0
Af1(s/s):                   0.0
week:                       388

******** Week 388 almanac for PRN-02 ********
ID:                         2
Health:                     000
Eccentricity:               0.002
Time of Applicability(s):  147456.0
Orbital Inclination(rad):   0.96
Rate of Right Ascen(r/s):  -0.000000008
SQRT(A)  (m 1/2):           5153.7
Right Ascen at Week(rad):   0.2
Argument of Perigee(rad):   0.3
Mean Anom(rad):             0.4
Af0(s):                     0.0
Af1(s/s):                   0.0
week:                       388
"""


def _write_authority(tmp_path: Path) -> None:
    source = Path("scenarios/orekit_design_smoke.yaml").read_text(encoding="utf-8")
    (tmp_path / "authority.yaml").write_text(source, encoding="utf-8")


def _fake_convert(self, **kwargs) -> MeanConversionResult:
    prn = int(kwargs["prn"])
    return MeanConversionResult(
        mean_orbit=MeanOrbit(
            a_m=26_560_000.0 + prn,
            ex=0.001 * prn,
            ey=0.0,
            ix=0.2,
            iy=0.0,
            lambda_rad=0.1 * prn,
            definition=MeanElementDefinition(
                theory="orekit-dsst",
                force_model_fingerprint=kwargs["force_model"].fingerprint(),
            ),
        ),
        backend_metadata={
            "source_authority": "GPS-ALMANAC-OREKIT-GNSS",
            "gps_prn": str(prn),
        },
    )


def test_all_gps_almanac_records_replace_template_constellation(tmp_path: Path, monkeypatch) -> None:
    _write_authority(tmp_path)
    monkeypatch.setattr(
        gnss_almanac_input.OrekitGpsAlmanacMeanConversionClient,
        "convert",
        _fake_convert,
    )
    result = create_gps_almanac_derived_scenario(
        tmp_path,
        GpsAlmanacCreateRequest(
            filename="current_yuma.alm",
            content_text=YUMA_TWO,
            source_format=GnssAlmanacFormat.GPS_YUMA,
            source_scenario_name="authority.yaml",
            template_satellite_id="SYNTH-REF",
            selection="all",
            target_scenario_name="gps-full.yaml",
            new_scenario_id="gps-full",
        ),
    )
    child = load_scenario(tmp_path / "gps-full.yaml")
    assert result["selected_prns"] == [1, 2]
    assert result["satellite_count"] == 2
    assert [sat.satellite_id for sat in child.constellation.satellites] == ["GPS-01", "GPS-02"]
    assert all(sat.plane_id == "ALMANAC-UNASSIGNED" for sat in child.constellation.satellites)
    assert all(sat.role == "reference" for sat in child.constellation.satellites)
    assert child.maneuvers == ()
    assert child.digital_twin is not None
    assert child.digital_twin.lineage is not None
    assert child.digital_twin.lineage.source_record_id == "GPS:1,2"


def test_selected_gps_prns_create_only_requested_spacecraft(tmp_path: Path, monkeypatch) -> None:
    _write_authority(tmp_path)
    monkeypatch.setattr(
        gnss_almanac_input.OrekitGpsAlmanacMeanConversionClient,
        "convert",
        _fake_convert,
    )
    result = create_gps_almanac_derived_scenario(
        tmp_path,
        GpsAlmanacCreateRequest(
            filename="current_yuma.alm",
            content_text=YUMA_TWO,
            source_format=GnssAlmanacFormat.GPS_YUMA,
            source_scenario_name="authority.yaml",
            template_satellite_id="SYNTH-REF",
            selection="selected",
            prns=[2],
            target_scenario_name="gps-selected.yaml",
            new_scenario_id="gps-selected",
        ),
    )
    child = load_scenario(tmp_path / "gps-selected.yaml")
    assert result["selected_prns"] == [2]
    assert [sat.satellite_id for sat in child.constellation.satellites] == ["GPS-02"]


def test_gps_almanac_ui_uses_explicit_authority_and_multi_selection() -> None:
    assert 'id="gnssAlmanacAuthority"' in GNSS_ALMANAC_CARD
    assert 'id="gnssAlmanacTemplate"' in GNSS_ALMANAC_CARD
    assert 'id="gnssAlmanacSelection"' in GNSS_ALMANAC_CARD
    assert 'id="gnssAlmanacPrns"' in GNSS_ALMANAC_CARD
    assert "source_scenario_name:gnssAlmanacAuthority.value" in GNSS_ALMANAC_SCRIPT
    assert "template_satellite_id:gnssAlmanacTemplate.value" in GNSS_ALMANAC_SCRIPT
    assert "source_scenario_name:scenario.value" not in GNSS_ALMANAC_SCRIPT
