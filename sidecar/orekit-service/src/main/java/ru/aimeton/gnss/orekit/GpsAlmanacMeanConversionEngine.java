package ru.aimeton.gnss.orekit;

import static ru.aimeton.gnss.orekit.ApiModels.*;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import javax.xml.parsers.DocumentBuilderFactory;

import org.orekit.frames.Frame;
import org.orekit.gnss.SEMParser;
import org.orekit.gnss.SatelliteSystem;
import org.orekit.gnss.YUMAParser;
import org.orekit.orbits.KeplerianOrbit;
import org.orekit.propagation.analytical.gnss.GNSSPropagator;
import org.orekit.propagation.analytical.gnss.GNSSPropagatorBuilder;
import org.orekit.propagation.analytical.gnss.data.GPSAlmanac;
import org.orekit.propagation.analytical.gnss.data.GalileoAlmanac;
import org.orekit.time.AbsoluteDate;
import org.orekit.time.GNSSDate;
import org.orekit.utils.Constants;
import org.orekit.utils.TimeStampedPVCoordinates;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

final class GpsAlmanacMeanConversionEngine {
    private static final double GALILEO_MAX_ISSUE_DISTANCE_S = 14.0 * Constants.JULIAN_DAY;

    private final OrekitRuntime runtime;
    private final MeanConversionEngine meanConversionEngine;

    GpsAlmanacMeanConversionEngine(OrekitRuntime runtime) {
        this.runtime = runtime;
        this.meanConversionEngine = new MeanConversionEngine(runtime);
    }

    MeanConversionResult convert(GpsAlmanacToMeanRequest request) {
        validateRequest(request);
        if ("galileo-gsc".equals(request.sourceFormat())) {
            return convertGalileoGsc(request);
        }

        GPSAlmanac almanac = parseAndSelectGps(request);
        AbsoluteDate targetDate = runtime.date(request.targetEpoch(), request.targetTimeScale());
        Frame outputFrame = runtime.propagationFrame(request.frame());
        GNSSPropagator propagator = new GNSSPropagatorBuilder(almanac, runtime.context().getFrames())
                .eci(outputFrame)
                .ecef(runtime.bodyFixedFrame())
                .mass(request.spacecraft().initialMassKg())
                .build();
        TimeStampedPVCoordinates pv = propagator.getPVCoordinates(targetDate, outputFrame);
        KeplerianOrbit osculating = new KeplerianOrbit(pv, outputFrame, request.forceModel().muM3S2());

        MeanConversionResult result = convertOsculating(request, targetDate, outputFrame, osculating);
        Map<String, String> metadata = new LinkedHashMap<>(result.backendMetadata());
        metadata.put("source_authority", "GPS-ALMANAC-OREKIT-GNSS");
        metadata.put("almanac_source_format", request.sourceFormat());
        metadata.put("almanac_source_name", request.sourceName());
        metadata.put("gps_prn", Integer.toString(request.prn()));
        metadata.put("almanac_epoch", almanac.getDate().toString(runtime.timeScale("GPS")));
        metadata.put("gnss_target_epoch", targetDate.toString(runtime.timeScale(request.targetTimeScale())));
        metadata.put("gnss_target_time_scale", request.targetTimeScale());
        metadata.put("input_representation", "gps-almanac-via-orekit-gnss-osculating-pv");
        metadata.put("conversion_chain", "YUMA/SEM->Orekit-GPSAlmanac->Orekit-GNSS-propagator@target-epoch->osculating-PV->inertial-frame->Orekit-DSST-mean");
        return new MeanConversionResult(result.meanOrbit(), metadata);
    }

    private MeanConversionResult convertGalileoGsc(GpsAlmanacToMeanRequest request) {
        GalileoParsed parsed = parseGalileoGsc(request);
        AbsoluteDate requestedTarget = runtime.date(request.targetEpoch(), request.targetTimeScale());
        if (Math.abs(requestedTarget.durationFrom(parsed.issueDate())) > 1.0) {
            throw new IllegalArgumentException(
                    "Galileo GSC target_epoch must equal the XML issueDate; modelling-authority epoch must not leak into almanac promotion");
        }
        AbsoluteDate targetDate = parsed.issueDate();
        Frame outputFrame = runtime.propagationFrame(request.frame());
        GNSSPropagator propagator = new GNSSPropagatorBuilder(parsed.almanac(), runtime.context().getFrames())
                .eci(outputFrame)
                .ecef(runtime.bodyFixedFrame())
                .mass(request.spacecraft().initialMassKg())
                .build();
        TimeStampedPVCoordinates pv = propagator.getPVCoordinates(targetDate, outputFrame);
        KeplerianOrbit osculating = new KeplerianOrbit(pv, outputFrame, request.forceModel().muM3S2());
        MeanConversionResult result = convertOsculating(request, targetDate, outputFrame, osculating);

        Map<String, String> metadata = new LinkedHashMap<>(result.backendMetadata());
        metadata.put("source_authority", "GALILEO-GSC-ALMANAC-OREKIT-GNSS");
        metadata.put("almanac_source_format", "galileo-gsc");
        metadata.put("almanac_source_name", request.sourceName());
        metadata.put("galileo_svid", Integer.toString(request.prn()));
        metadata.put("galileo_week", Integer.toString(parsed.fullWeek()));
        metadata.put("gsc_issue_date", parsed.issueDate().toString(runtime.timeScale("UTC")));
        metadata.put("almanac_epoch", parsed.almanac().getDate().toString(runtime.context().getTimeScales().getGST()));
        metadata.put("gnss_target_epoch", targetDate.toString(runtime.timeScale(request.targetTimeScale())));
        metadata.put("gnss_target_time_scale", request.targetTimeScale());
        metadata.put("input_representation", "galileo-gsc-almanac-via-orekit-gnss-osculating-pv");
        metadata.put("conversion_chain", "GSC-XML->Orekit-GalileoAlmanac->Orekit-GNSS-propagator@issueDate->osculating-PV->inertial-frame->Orekit-DSST-mean");
        return new MeanConversionResult(result.meanOrbit(), metadata);
    }

    private MeanConversionResult convertOsculating(
            GpsAlmanacToMeanRequest request,
            AbsoluteDate targetDate,
            Frame outputFrame,
            KeplerianOrbit osculating) {
        OsculatingToMeanRequest delegated = new OsculatingToMeanRequest(
                targetDate.toString(runtime.timeScale(request.targetTimeScale())),
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
        return meanConversionEngine.convert(delegated);
    }

    private GPSAlmanac parseAndSelectGps(GpsAlmanacToMeanRequest request) {
        List<GPSAlmanac> almanacs;
        byte[] bytes = request.sourceText().getBytes(StandardCharsets.UTF_8);
        try (var input = new ByteArrayInputStream(bytes)) {
            switch (request.sourceFormat()) {
                case "gps-yuma" -> {
                    YUMAParser parser = new YUMAParser(
                            null,
                            runtime.context().getDataProvidersManager(),
                            runtime.context().getTimeScales());
                    parser.loadData(input, request.sourceName());
                    almanacs = parser.getAlmanacs();
                }
                case "gps-sem" -> {
                    SEMParser parser = new SEMParser(
                            null,
                            runtime.context().getDataProvidersManager(),
                            runtime.context().getTimeScales());
                    parser.loadData(input, request.sourceName());
                    almanacs = parser.getAlmanacs();
                }
                default -> throw new UnsupportedOperationException(
                        "almanac authority supports gps-yuma, gps-sem, or galileo-gsc");
            }
        } catch (Exception exception) {
            if (exception instanceof IllegalArgumentException || exception instanceof UnsupportedOperationException) {
                throw (RuntimeException) exception;
            }
            throw new IllegalArgumentException("Orekit could not parse GPS almanac source: " + exception.getMessage(), exception);
        }
        return almanacs.stream()
                .filter(item -> item.getPRN() == request.prn())
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException(
                        "requested GPS PRN is absent from parsed almanac: " + request.prn()));
    }

    private GalileoParsed parseGalileoGsc(GpsAlmanacToMeanRequest request) {
        try {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setNamespaceAware(true);
            factory.setXIncludeAware(false);
            factory.setExpandEntityReferences(false);
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
            factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
            Document document;
            try (var input = new ByteArrayInputStream(request.sourceText().getBytes(StandardCharsets.UTF_8))) {
                document = factory.newDocumentBuilder().parse(input);
            }

            String issueText = descendantText(document.getDocumentElement(), "issueDate");
            AbsoluteDate issueDate = runtime.date(issueText, "UTC");
            Element record = findGalileoRecord(document, request.prn());
            int wnaMod4 = integer(record, "wna");
            int fullWeek = resolveGalileoWeek(issueDate, wnaMod4);

            GalileoAlmanac almanac = new GalileoAlmanac(
                    runtime.context().getTimeScales(), SatelliteSystem.GALILEO);
            almanac.setPRN(request.prn());
            almanac.setDeltaSqrtA(number(record, "aSqRoot"));
            almanac.setE(number(record, "ecc"));
            almanac.setDeltaInc(number(record, "deltai") * Math.PI);
            almanac.setOmega0(number(record, "omega0") * Math.PI);
            almanac.setOmegaDot(number(record, "omegaDot") * Math.PI);
            almanac.setPa(number(record, "w") * Math.PI);
            almanac.setM0(number(record, "m0") * Math.PI);
            almanac.setAf0(number(record, "af0"));
            almanac.setAf1(number(record, "af1"));
            almanac.setIOD(integer(record, "iod"));
            almanac.setWeek(fullWeek);
            almanac.setTime(number(record, "t0a"));
            almanac.setHealthE5a(integer(record, "statusE5a"));
            almanac.setHealthE5b(integer(record, "statusE5b"));
            almanac.setHealthE1(integer(record, "statusE1B"));

            if (Math.abs(almanac.getDate().durationFrom(issueDate)) > GALILEO_MAX_ISSUE_DISTANCE_S) {
                throw new IllegalArgumentException(
                        "resolved Galileo almanac epoch is more than 14 days from GSC issueDate");
            }
            return new GalileoParsed(almanac, issueDate, fullWeek);
        } catch (RuntimeException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new IllegalArgumentException(
                    "Orekit could not parse Galileo GSC almanac source: " + exception.getMessage(), exception);
        }
    }

    private int resolveGalileoWeek(AbsoluteDate issueDate, int wnaMod4) {
        if (wnaMod4 < 0 || wnaMod4 > 3) {
            throw new IllegalArgumentException("Galileo WNa modulo-4 must be in 0..3");
        }
        int referenceWeek = new GNSSDate(
                issueDate, SatelliteSystem.GALILEO, runtime.context().getTimeScales()).getWeekNumber();
        int delta = Math.floorMod(wnaMod4 - Math.floorMod(referenceWeek, 4), 4);
        if (delta > 2) {
            delta -= 4;
        }
        return referenceWeek + delta;
    }

    private static Element findGalileoRecord(Document document, int svid) {
        NodeList records = document.getElementsByTagNameNS("*", "svAlmanac");
        if (records.getLength() == 0) {
            records = document.getElementsByTagName("svAlmanac");
        }
        for (int index = 0; index < records.getLength(); index++) {
            Element element = (Element) records.item(index);
            if (integer(element, "SVID") == svid) {
                return element;
            }
        }
        throw new IllegalArgumentException("requested Galileo SVID is absent from GSC XML: " + svid);
    }

    private static double number(Element element, String tag) {
        try {
            return Double.parseDouble(descendantText(element, tag).replace('D', 'E').replace('d', 'e'));
        } catch (NumberFormatException exception) {
            throw new IllegalArgumentException("invalid Galileo GSC numeric field: " + tag, exception);
        }
    }

    private static int integer(Element element, String tag) {
        try {
            return Integer.parseInt(descendantText(element, tag));
        } catch (NumberFormatException exception) {
            throw new IllegalArgumentException("invalid Galileo GSC integer field: " + tag, exception);
        }
    }

    private static String descendantText(Element element, String tag) {
        NodeList nodes = element.getElementsByTagNameNS("*", tag);
        if (nodes.getLength() == 0) {
            nodes = element.getElementsByTagName(tag);
        }
        if (nodes.getLength() == 0) {
            throw new IllegalArgumentException("Galileo GSC record missing field: " + tag);
        }
        String text = nodes.item(0).getTextContent();
        if (text == null || text.isBlank()) {
            throw new IllegalArgumentException("Galileo GSC field is blank: " + tag);
        }
        return text.trim();
    }

    private static void validateRequest(GpsAlmanacToMeanRequest request) {
        if (request.sourceFormat() == null || request.sourceFormat().isBlank()) {
            throw new IllegalArgumentException("source_format is mandatory");
        }
        if (request.sourceName() == null || request.sourceName().isBlank()) {
            throw new IllegalArgumentException("source_name is mandatory");
        }
        if (request.sourceText() == null || request.sourceText().isBlank()) {
            throw new IllegalArgumentException("source_text is mandatory");
        }
        if (request.prn() <= 0) {
            throw new IllegalArgumentException("prn/SVID must be positive");
        }
        if (request.frame() == null || request.frame().isBlank()) {
            throw new IllegalArgumentException("frame is mandatory");
        }
        if (request.targetEpoch() == null || request.targetEpoch().isBlank()) {
            throw new IllegalArgumentException("target_epoch is mandatory");
        }
        if (request.targetTimeScale() == null || request.targetTimeScale().isBlank()) {
            throw new IllegalArgumentException("target_time_scale is mandatory");
        }
        if (request.spacecraft() == null) {
            throw new IllegalArgumentException("spacecraft is mandatory");
        }
        if (request.forceModel() == null) {
            throw new IllegalArgumentException("force_model is mandatory");
        }
        if (request.forceModelFingerprint() == null || request.forceModelFingerprint().isBlank()) {
            throw new IllegalArgumentException("force_model_fingerprint is mandatory");
        }
    }

    private record GalileoParsed(GalileoAlmanac almanac, AbsoluteDate issueDate, int fullWeek) {}
}
