package ru.aimeton.gnss.orekit;

import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.annotation.JsonProperty;

final class ApiModels {
    private ApiModels() {}

    record MeanElementDefinition(String representation, String theory, String forceModelFingerprint) {}

    record MeanOrbit(
            double aM,
            double ex,
            double ey,
            double ix,
            double iy,
            double lambdaRad,
            MeanElementDefinition definition) {}

    record SpacecraftModel(
            double dryMassKg,
            double propellantMassKg,
            double ispS,
            double areaM2,
            double cr) {
        double initialMassKg() {
            return dryMassKg + propellantMassKg;
        }
    }

    record SatelliteSpec(
            String satelliteId,
            String planeId,
            String role,
            String referenceId,
            MeanOrbit meanOrbit,
            SpacecraftModel spacecraft) {}

    record ForceModel(
            String mode,
            String gravityModel,
            double muM3S2,
            double referenceRadiusM,
            double flattening,
            double j2,
            double earthRotationRateRadS,
            int gravityDegree,
            int gravityOrder,
            boolean moon,
            boolean sun,
            boolean srp,
            boolean tides,
            boolean relativity) {}

    record Integrator(
            double minStepS,
            double maxStepS,
            double absTolerance,
            double relTolerance) {}

    record Maneuver(
            String satelliteId,
            double timeS,
            @JsonProperty("dv_rtn_m_s") List<Double> dvRtnMS) {}

    record PropagationRequest(
            String scenarioId,
            String epoch,
            String frame,
            String timeScale,
            List<SatelliteSpec> satellites,
            List<Maneuver> maneuvers,
            double durationS,
            double outputStepS,
            ForceModel forceModel,
            Integrator integrator,
            int seed,
            String forceModelFingerprint) {}

    record OsculatingState(
            double epochS,
            List<Double> rM,
            @JsonProperty("v_m_s") List<Double> vMS) {}

    record PropagationResult(
            String backend,
            String backendVersion,
            String forceModelFingerprint,
            Map<String, String> backendMetadata,
            List<Double> timesS,
            Map<String, List<MeanOrbit>> meanOrbits,
            Map<String, List<OsculatingState>> cartesianStates) {}

    record OsculatingToMeanRequest(
            String epoch,
            String frame,
            String timeScale,
            double aM,
            double e,
            double iRad,
            double paRad,
            double raanRad,
            double anomalyRad,
            String anomalyType,
            SpacecraftModel spacecraft,
            ForceModel forceModel,
            String forceModelFingerprint) {}

    record TleToMeanRequest(
            String line1,
            String line2,
            String frame,
            String targetEpoch,
            String targetTimeScale,
            SpacecraftModel spacecraft,
            ForceModel forceModel,
            String forceModelFingerprint) {}

    record GpsAlmanacToMeanRequest(
            String sourceFormat,
            String sourceName,
            String sourceText,
            int prn,
            String frame,
            String targetEpoch,
            String targetTimeScale,
            SpacecraftModel spacecraft,
            ForceModel forceModel,
            String forceModelFingerprint) {}

    record GlonassAlmanacToMeanRequest(
            String sourceName,
            int slot,
            int frequencyChannel,
            int health,
            String referenceDate,
            double referenceTimeS,
            double lambdaRad,
            double deltaIRad,
            double argumentOfPerigeeRad,
            double eccentricity,
            double deltaTS,
            double deltaTDot,
            double gloToUtcS,
            double gpsToGloS,
            double gloTimeOffsetS,
            String frame,
            String targetEpoch,
            String targetTimeScale,
            SpacecraftModel spacecraft,
            ForceModel forceModel,
            String forceModelFingerprint) {}

    record RinexGlonassToMeanRequest(
            String sourceName,
            String sourceText,
            String frame,
            String targetEpoch,
            String targetTimeScale,
            double maxEphemerisAgeS,
            double glonassPropagationStepS,
            SpacecraftModel spacecraft,
            ForceModel forceModel,
            String forceModelFingerprint) {}

    record RinexGlonassSatelliteMean(
            String satelliteId,
            int prn,
            int frequencyChannel,
            int healthFlags,
            String ephemerisEpoch,
            double ephemerisAgeS,
            MeanOrbit meanOrbit,
            Map<String, String> backendMetadata) {}

    record RinexGlonassToMeanResult(
            List<RinexGlonassSatelliteMean> satellites,
            Map<String, String> backendMetadata) {}

    record RinexGnssToMeanRequest(
            String system,
            String sourceName,
            String sourceText,
            String frame,
            String targetEpoch,
            String targetTimeScale,
            double maxEphemerisAgeS,
            SpacecraftModel spacecraft,
            ForceModel forceModel,
            String forceModelFingerprint) {}

    record RinexGnssSatelliteMean(
            String satelliteId,
            int prn,
            String ephemerisEpoch,
            double ephemerisAgeS,
            MeanOrbit meanOrbit,
            Map<String, String> backendMetadata) {}

    record RinexGnssToMeanResult(
            List<RinexGnssSatelliteMean> satellites,
            Map<String, String> backendMetadata) {}

    record MeanConversionResult(
            MeanOrbit meanOrbit,
            Map<String, String> backendMetadata) {}
}
