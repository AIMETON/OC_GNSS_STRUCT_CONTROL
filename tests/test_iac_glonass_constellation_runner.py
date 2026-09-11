from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient

from constellation_control.adapters.iac_gnss_tables import IacDataset, parse_iac_text
from constellation_control.application.run import load_scenario
from constellation_control.preview.gravity_release_app import create_preview_app, render_preview_page_for_test
from constellation_control.preview.iac_glonass_constellation_runner import (
    IacGlonassConstellationRequest,
    build_iac_glonass_constellation,
)


IAC_GLONASS_TEXT = """NS\tДата\tTΩ\tTоб\te\ti\tLΩ\tω\tδt2\tnl\tΔT
1\t08.08.26\t5679.75\t40543.81\t0.00039\t65.037445\t134.09329\t37.58972\t-1.7929077E-4\t1\t-4.272461E-4
2\t08.08.26\t10610.219\t40543.953\t0.00202\t65.446\t116.15312\t-135.8899\t-1.1444092E-5\t-4\t-5.493164E-4
"""


def _request() -> IacGlonassConstellationRequest:
    return IacGlonassConstellationRequest(
        source_mode="offline",
        filename="glonass-iac.tsv",
        content_text=IAC_GLONASS_TEXT,
        modelling_authority_scenario_name="orekit_design_smoke.yaml",
        template_satellite_id=None,
        health=0,
        glo_to_utc_s=0.0,
        gps_to_glo_s=0.0,
        glo_time_offset_s=0.0,
        supplement_confirmed=True,
        target_scenario_name="iac-glonass-current.yaml",
        new_scenario_id="iac-glonass-current",
    )


def test_release_ui_closes_iac_to_full_glonass_scenario_chain() -> None:
    page = render_preview_page_for_test()
    assert 'id="iacGlonassConstellationCard"' in page
    assert "/api/iac-glonass-constellation/create" in page
    assert 'id="iacGloConstAuthority"' in page
    assert "modelling_authority_scenario_name:authority" in page
    assert "Build full GLONASS constellation" in page
    assert "iacGloPromoteAll" in page
    assert 'id="iacGloConstUtc" type="number" step="any" value="0"' in page
    assert "supplement_confirmed:true" in page
    assert "try{" in page
    assert "finally{" in page
    assert "scenario.value=d.scenario_name;await loadScenario()" in page


def test_api_fails_closed_without_explicit_supplement_confirmation() -> None:
    client = TestClient(create_preview_app())
    payload = _request().model_dump(mode="json")
    payload["supplement_confirmed"] = False
    response = client.post("/api/iac-glonass-constellation/create", json=payload)
    assert response.status_code == 422


def test_api_requires_explicit_modelling_authority() -> None:
    client = TestClient(create_preview_app())
    payload = _request().model_dump(mode="json")
    payload["modelling_authority_scenario_name"] = ""
    response = client.post("/api/iac-glonass-constellation/create", json=payload)
    assert response.status_code == 422


def test_full_iac_constellation_replaces_template_constellation(tmp_path: Path, monkeypatch) -> None:
    source = load_scenario(Path("scenarios/orekit_design_smoke.yaml"))
    source_path = tmp_path / "orekit_design_smoke.yaml"
    source_path.write_text(
        yaml.safe_dump(source.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    template = source.constellation.satellites[0]
    assert template.mean_orbit is not None
    table = parse_iac_text(IacDataset.GLONASS_ALMANAC, IAC_GLONASS_TEXT)
    monkeypatch.setattr(
        "constellation_control.preview.iac_glonass_constellation_runner._table",
        lambda request: table,
    )

    class FakeClient:
        def __init__(self, base_url: str):
            assert base_url == source.orekit_sidecar_url

        def convert(self, **kwargs):
            return SimpleNamespace(
                mean_orbit=template.mean_orbit,
                backend_metadata={"glonass_slot": str(kwargs["slot"])},
            )

    monkeypatch.setattr(
        "constellation_control.preview.iac_glonass_constellation_runner.OrekitGlonassAlmanacMeanConversionClient",
        FakeClient,
    )
    result = build_iac_glonass_constellation(tmp_path, _request())
    assert result["runnable"] is True
    assert result["satellite_count"] == 2
    assert result["satellite_ids"] == ["GLO-01", "GLO-02"]
    assert result["source_sha256"] == table.source_sha256
    assert result["modelling_authority_scenario_name"] == "orekit_design_smoke.yaml"
    assert result["template_satellite_id"] == template.satellite_id

    child = load_scenario(tmp_path / "iac-glonass-current.yaml")
    assert [sat.satellite_id for sat in child.constellation.satellites] == ["GLO-01", "GLO-02"]
    assert all(sat.plane_id == "ALMANAC-UNASSIGNED" for sat in child.constellation.satellites)
    assert all(sat.spacecraft == template.spacecraft for sat in child.constellation.satellites)
    assert child.maneuvers == ()
    assert child.digital_twin is not None
    assert child.digital_twin.lineage is not None
    assert child.digital_twin.lineage.source_sha256 == table.source_sha256
    assert child.digital_twin.lineage.source_record_id == "GLO:2"
    assert "tGlo2UTC=0.0" in child.digital_twin.lineage.authority
    assert "modelling_authority=orekit_design_smoke.yaml" in child.digital_twin.lineage.authority
