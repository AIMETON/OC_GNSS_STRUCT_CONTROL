from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from constellation_control.preview.consolidated_release_app import (
    create_preview_app as create_consolidated_preview_app,
    render_preview_page_for_test as render_consolidated_page,
)
from constellation_control.preview.igs_constellation_input import (
    IGS_CONSTELLATION_CARD,
    IGS_CONSTELLATION_SCRIPT,
    install_igs_constellation_routes,
)
from constellation_control.preview.glonass_rinex_runner import (
    GLONASS_RINEX_CARD,
    GLONASS_RINEX_SCRIPT,
    install_glonass_rinex_runner_routes,
)
from constellation_control.preview.gravity_model_ui import (
    GRAVITY_MODEL_CARD,
    GRAVITY_MODEL_SCRIPT,
    install_gravity_model_routes,
)
from constellation_control.preview.iac_glonass_constellation_runner import (
    IAC_GLONASS_CONSTELLATION_CARD,
    IAC_GLONASS_CONSTELLATION_SCRIPT,
    install_iac_glonass_constellation_routes,
)
from constellation_control.preview.iac_glonass_runner import (
    IAC_GLONASS_RUNNER_CARD,
    IAC_GLONASS_RUNNER_SCRIPT,
    install_iac_glonass_runner_routes,
)
from constellation_control.preview.mission_template_policy import (
    MISSION_TEMPLATE_POLICY_SCRIPT,
    install_mission_template_policy_routes,
)
from constellation_control.preview.mixed_gnss_runner import (
    MIXED_GNSS_RUNNER_CARD,
    MIXED_GNSS_RUNNER_SCRIPT,
    install_mixed_gnss_runner_routes,
)
from constellation_control.preview.navcen_gps_runner import (
    NAVCEN_GPS_RUNNER_CARD,
    NAVCEN_GPS_RUNNER_SCRIPT,
    install_navcen_gps_runner_routes,
)
from constellation_control.preview.operator_tabs import (
    OPERATOR_TABS_CARD,
    OPERATOR_TABS_SCRIPT,
    OPERATOR_TABS_STYLE,
)
from constellation_control.preview.scenario_workspace import (
    SCENARIO_VARIANT_CARD,
    SCENARIO_VARIANT_SCRIPT,
    install_scenario_variant_routes,
)
from constellation_control.version import __version__ as PREVIEW_VERSION


def render_preview_page_for_test() -> str:
    page = re.sub(
        r"Engineering Preview \d+\.\d+\.\d+",
        f"Engineering Preview {PREVIEW_VERSION}",
        render_consolidated_page(),
    )
    page = page.replace("</head>", f"{OPERATOR_TABS_STYLE}</head>", 1)
    page = page.replace("<section>", f"<section>{OPERATOR_TABS_CARD}", 1)
    page = page.replace(
        "</section></main>",
        (
            f"{IGS_CONSTELLATION_CARD}"
            f"{GLONASS_RINEX_CARD}"
            f"{IAC_GLONASS_RUNNER_CARD}"
            f"{IAC_GLONASS_CONSTELLATION_CARD}"
            f"{NAVCEN_GPS_RUNNER_CARD}"
            f"{MIXED_GNSS_RUNNER_CARD}"
            f"{SCENARIO_VARIANT_CARD}"
            f"{GRAVITY_MODEL_CARD}</section></main>"
        ),
        1,
    )
    page = page.replace(
        "bootstrap().catch(e=>setStatus(String(e),'danger'));",
        f"{IGS_CONSTELLATION_SCRIPT}\n"
        f"{GLONASS_RINEX_SCRIPT}\n"
        f"{IAC_GLONASS_RUNNER_SCRIPT}\n"
        f"{IAC_GLONASS_CONSTELLATION_SCRIPT}\n"
        f"{NAVCEN_GPS_RUNNER_SCRIPT}\n"
        f"{MIXED_GNSS_RUNNER_SCRIPT}\n"
        f"{SCENARIO_VARIANT_SCRIPT}\n"
        f"{GRAVITY_MODEL_SCRIPT}\n"
        "const gravityBootstrap=bootstrap;"
        "bootstrap=async function(){"
        "await gravityBootstrap();"
        "if(typeof syncIgsTemplateScenarios==='function')syncIgsTemplateScenarios();"
        "if(typeof syncGravityModel==='function')syncGravityModel();"
        "if(typeof syncScenarioVariant==='function')syncScenarioVariant();"
        "if(typeof syncGlonassRinexTemplate==='function')syncGlonassRinexTemplate();"
        "if(typeof syncIacGlonassRunnerSatellites==='function')syncIacGlonassRunnerSatellites();"
        "if(typeof syncIacGloConstTemplate==='function')syncIacGloConstTemplate();"
        "if(typeof installIacGloIntakeBridge==='function')installIacGloIntakeBridge();"
        "if(typeof syncNavcenGpsSatellites==='function')syncNavcenGpsSatellites();"
        "if(typeof syncMixedGnssTemplateSatellites==='function')syncMixedGnssTemplateSatellites();"
        "};\n"
        f"{OPERATOR_TABS_SCRIPT}\n"
        f"{MISSION_TEMPLATE_POLICY_SCRIPT}\n"
        "bootstrap().catch(e=>setStatus(String(e),'danger'));",
        1,
    )
    return page


def create_preview_app(scenario_root: Path = Path("scenarios"), output_root: Path = Path("runs")) -> FastAPI:
    app = create_consolidated_preview_app(scenario_root, output_root)
    app.version = PREVIEW_VERSION
    app.router.routes[:] = [
        route for route in app.router.routes if getattr(route, "path", None) not in {"/", "/health"}
    ]

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(render_preview_page_for_test())

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "preview": PREVIEW_VERSION}

    install_igs_constellation_routes(app, scenario_root)
    install_mission_template_policy_routes(app, scenario_root)
    install_glonass_rinex_runner_routes(app, scenario_root)
    install_iac_glonass_runner_routes(app, scenario_root)
    install_iac_glonass_constellation_routes(app, scenario_root)
    install_navcen_gps_runner_routes(app, scenario_root)
    install_mixed_gnss_runner_routes(app, scenario_root)
    install_scenario_variant_routes(app, scenario_root)
    install_gravity_model_routes(app, scenario_root)
    return app