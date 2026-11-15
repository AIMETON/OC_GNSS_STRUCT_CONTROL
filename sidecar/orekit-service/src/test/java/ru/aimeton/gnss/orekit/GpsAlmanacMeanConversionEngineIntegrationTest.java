package ru.aimeton.gnss.orekit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static ru.aimeton.gnss.orekit.ApiModels.*;

import java.nio.file.Path;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

final class GpsAlmanacMeanConversionEngineIntegrationTest {
    private static final String YUMA = """
            ******** Week 2295 almanac for PRN-01 ********
            ID:                         01
            Health:                     000
            Eccentricity:               0.0036592484
            Time of Applicability(s):  589824.0000
            Orbital Inclination(rad):   0.9599310886
            Rate of Right Ascen(r/s):  -0.0000000080
            SQRT(A)  (m 1/2):           5153.6552734375
            Right Ascen at Week(rad):   1.2345678900
            Argument of Perigee(rad):   0.5678901234
            Mean Anom(rad):             0.2345678901
            Af0(s):                     0.0001000000
            Af1(s/s):                   0.0000000000
            week:                       2295
            """;

    private static final String GALILEO_GSC = """
            <?xml version="1.0" encoding="UTF-8"?>
            <signalData>
              <header><GAL-header><issueDate>2026-09-11T09:59:59.0Z</issueDate></GAL-header></header>
              <body><Almanacs><svAlmanac>
                <SVID>02</SVID>
                <almanac>
                  <aSqRoot>0.0390625</aSqRoot>
                  <ecc>0.0003509521484375</ecc>
                  <deltai>-0.00518798828125</deltai>
                  <omega0>-0.69842529296875</omega0>
                  <omegaDot>-1.862645149230958878720099e-09</omegaDot>
                  <w>0.0283203125</w>
                  <m0>-0.73809814453125</m0>
                  <af0>6.866455078125e-05</af0>
                  <af1>3.63797880709171295166015625e-12</af1>
                  <iod>10</iod><t0a>466800</t0a><wna>3</wna>
                </almanac>
                <svFNavSignalStatus><statusE5a>0</statusE5a></svFNavSignalStatus>
                <svINavSignalStatus><statusE5b>0</statusE5b><statusE1B>0</statusE1B></svINavSignalStatus>
              </svAlmanac></Almanacs></body>
            </signalData>
            """;

    private static GpsAlmanacMeanConversionEngine engine;

    @BeforeAll
    static void setUpRuntime() throws Exception {
        String dataPath = System.getenv("OREKIT_DATA_PATH");
        if (dataPath == null || dataPath.isBlank()) {
            throw new IllegalStateException("OREKIT_DATA_PATH is required for Orekit integration tests");
        }
        engine = new GpsAlmanacMeanConversionEngine(new OrekitRuntime(Path.of(dataPath)));
    }

    @Test
    void convertsYumaThroughOrekitGnssToTargetEpochAndDsstMeanAuthority() {
        MeanConversionResult result = engine.convert(request("gps-yuma", "gps.alm", YUMA, 1, "2024-01-10T00:00:00Z"));
        assertEquals("GPS-ALMANAC-OREKIT-GNSS", result.backendMetadata().get("source_authority"));
        assertEquals("gps-yuma", result.backendMetadata().get("almanac_source_format"));
        assertEquals("1", result.backendMetadata().get("gps_prn"));
        assertTrue(result.backendMetadata().get("conversion_chain").contains("Orekit-GNSS-propagator"));
        assertEquals("orekit-dsst-13.1.7-from-osculating", result.meanOrbit().definition().theory());
        assertEquals("gps-almanac-authority-test-fingerprint", result.meanOrbit().definition().forceModelFingerprint());
        assertTrue(Double.isFinite(result.meanOrbit().aM()));
        assertTrue(result.meanOrbit().aM() > 0.0);
    }

    @Test
    void convertsNestedGscGalileoAlmanacAtXmlIssueDateAndDsstMeanAuthority() {
        MeanConversionResult result = engine.convert(request(
                "galileo-gsc", "2026-09-11.xml", GALILEO_GSC, 2, "2026-09-11T09:59:59Z"));
        assertEquals("GALILEO-GSC-ALMANAC-OREKIT-GNSS", result.backendMetadata().get("source_authority"));
        assertEquals("galileo-gsc", result.backendMetadata().get("almanac_source_format"));
        assertEquals("2", result.backendMetadata().get("galileo_svid"));
        assertTrue(result.backendMetadata().get("conversion_chain").contains("Orekit-GalileoAlmanac"));
        assertTrue(result.backendMetadata().get("conversion_chain").contains("Orekit-GNSS-propagator"));
        assertEquals("orekit-dsst-13.1.7-from-osculating", result.meanOrbit().definition().theory());
        assertTrue(Double.isFinite(result.meanOrbit().aM()));
        assertTrue(result.meanOrbit().aM() > 0.0);
    }

    @Test
    void rejectsGalileoPromotionAtModellingAuthorityEpochInsteadOfIssueDate() {
        IllegalArgumentException error = assertThrows(
                IllegalArgumentException.class,
                () -> engine.convert(request("galileo-gsc", "2026-09-11.xml", GALILEO_GSC, 2, "2024-01-10T00:00:00Z")));
        assertTrue(error.getMessage().contains("issueDate"));
    }

    @Test
    void rejectsUnsupportedGlonassSourceInsteadOfUsingGpsAuthority() {
        UnsupportedOperationException error = assertThrows(
                UnsupportedOperationException.class,
                () -> engine.convert(request("glonass-text", "gps.alm", YUMA, 1, "2024-01-10T00:00:00Z")));
        assertTrue(error.getMessage().contains("gps-yuma") || error.getMessage().contains("galileo-gsc"));
    }

    private static GpsAlmanacToMeanRequest request(
            String format, String sourceName, String sourceText, int prn, String targetEpoch) {
        SpacecraftModel spacecraft = new SpacecraftModel(500.0, 50.0, 220.0, 8.0, 1.3);
        ForceModel forceModel = new ForceModel(
                "design", OrekitRuntime.GRAVITY_MODEL, 3.986004418e14, 6_378_137.0,
                1.0 / 298.257223563, 0.00108262668, 7.2921150e-5, 8, 8,
                true, true, true, false, false);
        return new GpsAlmanacToMeanRequest(
                format, sourceName, sourceText, prn, "EME2000", targetEpoch, "UTC",
                spacecraft, forceModel, "gps-almanac-authority-test-fingerprint");
    }
}
