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
import org.orekit.propagation.analytical.gnss.data.AbstractNavigationMessage;
import org.orekit.time.AbsoluteDate;
import org.orekit.utils.TimeStampedPVCoordinates;

final class RinexGnssMeanConversionEngine {
    private final OrekitRuntime runtime;
    private final MeanConversionEngine meanConversion;

    RinexGnssMeanConversionEngine(OrekitRuntime runtime) {
        this.runtime = runtime;
        this.meanConversion = new MeanConversionEngine(runtime);
    }

    RinexGnssToMeanResult convert(RinexGnssToMeanRequest request) throws Exception {
        validate(request);
        DataSource source = new DataSource(
                request.sourceName(),
                (DataSource.ReaderOpener) () -> new StringReader(request.sourceText()));
        RinexNavigation navigation = new RinexNavigationParser(runtime.context().getTimeScales()).parse(source);

        Map<String, List<? extends AbstractNavigationMessage<?>>> messages = switch (request.system()) {
            case "GPS" -> preferLegacy(navigation.getGPSLegacyNavigationMessages(), navigation.getGPSCivilianNavigationMessages());
            case "Galileo" -> navigation.getGalileoNavigationMessages();
            case "BeiDou" -> preferLegacy(
                    navigation.getBeidouLegacyNavigationMessages(),
                    navigation.getBeidouCivilianNavigationMessages());
            default -> throw new IllegalArgumentException("unsupported RINEX GNSS system: " + request.system());
        };
        if (messages.isEmpty()) {
            throw new IllegalArgumentException("RINEX navigation file contains no " + request.system() + " messages");
        }

        AbsoluteDate target = runtime.date(request.targetEpoch(), request.targetTimeScale());
        Frame frame = runtime.propagationFrame(request.frame());
        List<RinexGnssSatelliteMean> satellites = new ArrayList<>();

        for (String satId : messages.keySet().stream().sorted().toList()) {
            List<? extends AbstractNavigationMessage<?>> candidates = messages.get(satId);
            AbstractNavigationMessage<?> selected = candidates.stream()
                    .min(Comparator.comparingDouble(item -> Math.abs(target.durationFrom(item.getDate()))))
                    .orElseThrow();
            double age = Math.abs(target.durationFrom(selected.getDate()));
            if (age > request.maxEphemerisAgeS()) {
                throw new IllegalArgumentException(
                        "nearest " + request.system() + " RINEX ephemeris is too old for " + satId
                                + ": age_s=" + age + " max=" + request.maxEphemerisAgeS());
            }

            var propagator = selected.getPropagator(runtime.context().getFrames());
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
            metadata.put("gnss_system", request.system());
            metadata.put("rinex_satellite_id", satId);
            metadata.put("prn", Integer.toString(selected.getPRN()));
            metadata.put("rinex_ephemeris_epoch", selected.getDate().toString());
            metadata.put("rinex_ephemeris_age_s", Double.toString(age));
            metadata.put(
                    "conversion_chain",
                    "BKG/IGS-RINEX-NAV->Orekit-RinexNavigationParser"
                            + "->Orekit-GNSSPropagator->Orekit-DSST-mean");

            satellites.add(new RinexGnssSatelliteMean(
                    satId,
                    selected.getPRN(),
                    selected.getDate().toString(),
                    age,
                    mean.meanOrbit(),
                    metadata));
        }

        Map<String, String> metadata = new LinkedHashMap<>();
        metadata.put("backend", "orekit-rinex-gnss-to-mean");
        metadata.put("orekit_version", OrekitRuntime.OREKIT_VERSION);
        metadata.put("orekit_data_revision", runtime.dataRevision());
        metadata.put("orekit_data_sha256", runtime.dataSha256());
        metadata.put("source_authority", "IGS-BKG-RINEX-NAV");
        metadata.put("gnss_system", request.system());
        metadata.put("source_name", request.sourceName());
        metadata.put("target_epoch", request.targetEpoch());
        metadata.put("target_time_scale", request.targetTimeScale());
        metadata.put("satellite_count", Integer.toString(satellites.size()));
        return new RinexGnssToMeanResult(satellites, metadata);
    }

    private static Map<String, List<? extends AbstractNavigationMessage<?>>> preferLegacy(
            Map<String, ? extends List<? extends AbstractNavigationMessage<?>>> legacy,
            Map<String, ? extends List<? extends AbstractNavigationMessage<?>>> civilian) {
        Map<String, List<? extends AbstractNavigationMessage<?>>> result = new LinkedHashMap<>();
        result.putAll(legacy);
        for (Map.Entry<String, ? extends List<? extends AbstractNavigationMessage<?>>> entry : civilian.entrySet()) {
            result.putIfAbsent(entry.getKey(), entry.getValue());
        }
        return result;
    }

    private static void validate(RinexGnssToMeanRequest request) {
        if (!List.of("GPS", "Galileo", "BeiDou").contains(request.system())) {
            throw new IllegalArgumentException("system must be GPS, Galileo or BeiDou");
        }
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
    }
}
