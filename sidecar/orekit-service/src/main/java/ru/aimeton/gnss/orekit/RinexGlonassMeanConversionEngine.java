package ru.aimeton.gnss.orekit;

import static ru.aimeton.gnss.orekit.ApiModels.*;

import java.io.StringReader;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.orekit.data.DataSource;
import org.orekit.files.rinex.navigation.RinexNavigation;
import org.orekit.files.rinex.navigation.RinexNavigationParser;
import org.orekit.frames.Frame;
import org.orekit.orbits.KeplerianOrbit;
import org.orekit.propagation.analytical.gnss.data.GLONASSNavigationMessage;
import org.orekit.time.AbsoluteDate;
import org.orekit.utils.TimeStampedPVCoordinates;

final class RinexGlonassMeanConversionEngine {
    private final OrekitRuntime runtime;
    private final MeanConversionEngine meanConversion;

    RinexGlonassMeanConversionEngine(OrekitRuntime runtime) {
        this.runtime = runtime;
        this.meanConversion = new MeanConversionEngine(runtime);
    }

    RinexGlonassToMeanResult convert(RinexGlonassToMeanRequest request) throws Exception {
        validate(request);
        DataSource source = new DataSource(
                request.sourceName(),
                (DataSource.ReaderOpener) () -> new StringReader(request.sourceText()));
        RinexNavigation navigation = new RinexNavigationParser(runtime.context().getTimeScales()).parse(source);
        Map<String, List<GLONASSNavigationMessage>> messages = navigation.getGlonassNavigationMessages();
        if (messages.isEmpty()) {
            throw new IllegalArgumentException("RINEX navigation file contains no GLONASS navigation messages");
        }

        AbsoluteDate target = runtime.date(request.targetEpoch(), request.targetTimeScale());
        Frame frame = runtime.propagationFrame(request.frame());
        List<RinexGlonassSatelliteMean> satellites = new ArrayList<>();

        for (String satId : messages.keySet().stream().sorted().toList()) {
            List<GLONASSNavigationMessage> candidates = messages.get(satId);
            GLONASSNavigationMessage selected = candidates.stream()
                    .min(Comparator.comparingDouble(item -> Math.abs(target.durationFrom(item.getDate()))))
                    .orElseThrow();
            double age = Math.abs(target.durationFrom(selected.getDate()));
            if (age > request.maxEphemerisAgeS()) {
                throw new IllegalArgumentException(
                        "nearest GLONASS RINEX ephemeris is too old for " + satId
                                + ": age_s=" + age + " max=" + request.maxEphemerisAgeS());
            }

            var propagator = selected.getPropagator(request.glonassPropagationStepS(), runtime.context());
            TimeStampedPVCoordinates pv = propagator.getPVCoordinates(target, frame);
            KeplerianOrbit osculating = new KeplerianOrbit(pv, frame, request.forceModel().muM3S2());

            OsculatingToMeanRequest meanRequest = new OsculatingToMeanRequest(
                    request.targetEpoch(),
                    request.frame(),
                    request.targetTimeScale(),
                    osculating.getA(),
                    osculating.getE(),
                    osculating.getI(),
                    osculating.getPerigeeArgument(),
                    osculating.getRightAscensionOfAscendingNode(),
                    osculating.getTrueAnomaly(),
                    "true",
                    request.spacecraft(),
                    request.forceModel(),
                    request.forceModelFingerprint());
            MeanConversionResult mean = meanConversion.convert(meanRequest);

            Map<String, String> metadata = new LinkedHashMap<>(mean.backendMetadata());
            metadata.put("source_authority", "IGS-BKG-RINEX-NAV");
            metadata.put("source_format", "RINEX-NAV");
            metadata.put("rinex_satellite_id", satId);
            metadata.put("glonass_prn", Integer.toString(selected.getPRN()));
            metadata.put("frequency_channel", Integer.toString(selected.getFrequencyNumber()));
            metadata.put("health_flags", Integer.toString(selected.getHealthFlags()));
            metadata.put("rinex_ephemeris_epoch", selected.getDate().toString());
            metadata.put("rinex_ephemeris_age_s", Double.toString(age));
            metadata.put(
                    "conversion_chain",
                    "BKG/IGS-RINEX-NAV->Orekit-RinexNavigationParser"
                            + "->Orekit-GLONASSNumericalPropagator->Orekit-DSST-mean");

            satellites.add(new RinexGlonassSatelliteMean(
                    satId,
                    selected.getPRN(),
                    selected.getFrequencyNumber(),
                    selected.getHealthFlags(),
                    selected.getDate().toString(),
                    age,
                    mean.meanOrbit(),
                    metadata));
        }

        Map<String, String> metadata = new LinkedHashMap<>();
        metadata.put("backend", "orekit-rinex-glonass-to-mean");
        metadata.put("orekit_version", OrekitRuntime.OREKIT_VERSION);
        metadata.put("orekit_data_revision", runtime.dataRevision());
        metadata.put("orekit_data_sha256", runtime.dataSha256());
        metadata.put("source_authority", "IGS-BKG-RINEX-NAV");
        metadata.put("source_name", request.sourceName());
        metadata.put("target_epoch", request.targetEpoch());
        metadata.put("target_time_scale", request.targetTimeScale());
        metadata.put("satellite_count", Integer.toString(satellites.size()));
        return new RinexGlonassToMeanResult(satellites, metadata);
    }

    private static void validate(RinexGlonassToMeanRequest request) {
        if (request.sourceName() == null || request.sourceName().isBlank()) {
            throw new IllegalArgumentException("source_name is mandatory");
        }
        if (request.sourceText() == null || request.sourceText().isBlank()) {
            throw new IllegalArgumentException("source_text is mandatory");
        }
        if (request.spacecraft() == null || request.forceModel() == null) {
            throw new IllegalArgumentException("spacecraft and force_model are mandatory");
        }
        if (request.forceModelFingerprint() == null || request.forceModelFingerprint().isBlank()) {
            throw new IllegalArgumentException("force_model_fingerprint is mandatory");
        }
        if (!(request.maxEphemerisAgeS() > 0.0) || !Double.isFinite(request.maxEphemerisAgeS())) {
            throw new IllegalArgumentException("max_ephemeris_age_s must be finite and positive");
        }
        if (!(request.glonassPropagationStepS() > 0.0) || !Double.isFinite(request.glonassPropagationStepS())) {
            throw new IllegalArgumentException("glonass_propagation_step_s must be finite and positive");
        }
    }
}
