from constellation_control.adapters.iac_glonass_almanac import normalize_iac_glonass_almanac
from constellation_control.adapters.iac_gnss_tables import IacDataset
from constellation_control.preview.gravity_release_app import create_preview_app, render_preview_page_for_test
from constellation_control.preview.iac_glonass_runner import IacGlonassRunnerAuthorityRequest, _table


IAC_GLONASS_TEXT = """NS\tДата\tTΩ\tTоб\te\ti\tLΩ\tω\tδt2\tnl\tΔT
1\t08.08.26\t5679.75\t40543.81\t0.00039\t65.037445\t134.09329\t37.58972\t-1.7929077E-4\t1\t-4.272461E-4
"""


def _request(**extra):
    payload = dict(
        source_mode="offline",
        filename="glonass-iac.tsv",
        content_text=IAC_GLONASS_TEXT,
        source_scenario_name="orekit_design_smoke.yaml",
        satellite_id="SYNTH-REF",
        slot=1,
        health=0,
        glo_to_utc_s=1.0,
        gps_to_glo_s=2.0,
        glo_time_offset_s=3.0,
    )
    payload.update(extra)
    return IacGlonassRunnerAuthorityRequest(**payload)


def test_offline_iac_source_normalizer_remains_available_to_full_constellation_path() -> None:
    table = _table(_request())
    almanac = normalize_iac_glonass_almanac(table)
    assert table.dataset == IacDataset.GLONASS_ALMANAC
    assert almanac.records[0].slot == 1
    assert almanac.records[0].frequency_channel == 1
    assert len(table.source_sha256) == 64


def test_obsolete_single_satellite_iac_runner_is_not_exposed() -> None:
    page = render_preview_page_for_test()
    assert 'id="iacGlonassRunnerCard"' not in page
    assert "/api/iac-glonass-runner/" not in page
    app = create_preview_app()
    paths = {getattr(route, "path", "") for route in app.router.routes}
    assert not any(path.startswith("/api/iac-glonass-runner/") for path in paths)
    assert "/api/iac-glonass-constellation/create" in paths
