package ru.aimeton.gnss.orekit;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.Executors;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

public final class OrekitServiceMain {
    private static final int MAX_REQUEST_BYTES = 16 * 1024 * 1024;
    private static final String PROGRESS_HEADER = "X-OC-GNSS-Progress-Id";
    private static final String PROGRESS_PATH = "/v1/progress";

    private OrekitServiceMain() {}

    public static void main(String[] args) throws Exception {
        String dataPath = requireEnv("OREKIT_DATA_PATH");
        int port = Integer.parseInt(System.getenv().getOrDefault("OREKIT_PORT", "8081"));
        OrekitRuntime runtime = new OrekitRuntime(Path.of(dataPath));
        PropagationEngine engine = new PropagationEngine(runtime);
        PropagationProgressRegistry progressRegistry = new PropagationProgressRegistry();
        MeanConversionEngine meanConversionEngine = new MeanConversionEngine(runtime);
        TleMeanConversionEngine tleMeanConversionEngine = new TleMeanConversionEngine(runtime);
        GpsAlmanacMeanConversionEngine gpsAlmanacMeanConversionEngine = new GpsAlmanacMeanConversionEngine(runtime);
        GlonassAlmanacMeanConversionEngine glonassAlmanacMeanConversionEngine = new GlonassAlmanacMeanConversionEngine(runtime);
        RinexGlonassMeanConversionEngine rinexGlonassMeanConversionEngine = new RinexGlonassMeanConversionEngine(runtime);
        RinexGnssMeanConversionEngine rinexGnssMeanConversionEngine = new RinexGnssMeanConversionEngine(runtime);
        ObjectMapper mapper = mapper();

        InetSocketAddress bindAddress = new InetSocketAddress(InetAddress.getByName("127.0.0.1"), port);
        HttpServer server = HttpServer.create(bindAddress, 32);
        server.createContext("/healthz", exchange -> handleHealth(exchange, mapper, runtime));
        server.createContext(
                "/v1/propagate",
                exchange -> handlePropagate(exchange, mapper, engine, progressRegistry));
        server.createContext(
                PROGRESS_PATH,
                exchange -> handlePropagationProgress(exchange, mapper, progressRegistry));
        server.createContext(
                "/v1/orbits/osculating-to-mean",
                exchange -> handleOsculatingToMean(exchange, mapper, meanConversionEngine));
        server.createContext(
                "/v1/orbits/tle-to-mean",
                exchange -> handleTleToMean(exchange, mapper, tleMeanConversionEngine));
        server.createContext(
                "/v1/orbits/gps-almanac-to-mean",
                exchange -> handleGpsAlmanacToMean(exchange, mapper, gpsAlmanacMeanConversionEngine));
        server.createContext(
                "/v1/orbits/glonass-almanac-to-mean",
                exchange -> handleGlonassAlmanacToMean(exchange, mapper, glonassAlmanacMeanConversionEngine));
        server.createContext(
                "/v1/orbits/rinex-glonass-to-mean",
                exchange -> handleRinexGlonassToMean(exchange, mapper, rinexGlonassMeanConversionEngine));
        server.createContext(
                "/v1/orbits/rinex-gnss-to-mean",
                exchange -> handleRinexGnssToMean(exchange, mapper, rinexGnssMeanConversionEngine));
        server.setExecutor(Executors.newFixedThreadPool(
                Math.max(2, Runtime.getRuntime().availableProcessors())));
        server.start();
        System.out.printf(
                "orekit-service listening on 127.0.0.1:%d orekit=%s gravity=%s data_revision=%s data_sha256=%s%n",
                port,
                OrekitRuntime.OREKIT_VERSION,
                runtime.gravityModel(),
                runtime.dataRevision(),
                runtime.dataSha256());
    }

    static ObjectMapper mapper() {
        return new ObjectMapper().setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
    }

    private static void handleHealth(HttpExchange exchange, ObjectMapper mapper, OrekitRuntime runtime)
            throws IOException {
        if (!"GET".equals(exchange.getRequestMethod())) {
            writeJson(exchange, mapper, 405, Map.of("error", "method_not_allowed"));
            return;
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "ok");
        body.put("backend", "orekit");
        body.put("orekit_version", OrekitRuntime.OREKIT_VERSION);
        body.put("orekit_data_revision", runtime.dataRevision());
        body.put("orekit_data_sha256", runtime.dataSha256());
        body.put("gravity_model", runtime.gravityModel());
        writeJson(exchange, mapper, 200, body);
    }

    private static void handlePropagate(
            HttpExchange exchange,
            ObjectMapper mapper,
            PropagationEngine engine,
            PropagationProgressRegistry progressRegistry) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            writeJson(exchange, mapper, 405, Map.of("error", "method_not_allowed"));
            return;
        }
        String telemetryId = exchange.getRequestHeaders().getFirst(PROGRESS_HEADER);
        try {
            byte[] body = readBody(exchange);
            ApiModels.PropagationRequest request = mapper.readValue(body, ApiModels.PropagationRequest.class);
            ApiModels.PropagationResult result;
            if (telemetryId == null || telemetryId.isBlank()) {
                result = engine.propagate(request);
            } else {
                progressRegistry.start(telemetryId);
                result = engine.propagate(request, event -> progressRegistry.update(telemetryId, event));
                progressRegistry.complete(telemetryId);
            }
            writeStreamingJson(exchange, mapper, 200, result);
        } catch (RequestTooLargeException exception) {
            failProgress(progressRegistry, telemetryId, "request_too_large");
            writeJson(exchange, mapper, 413, Map.of("error", "request_too_large"));
        } catch (IllegalArgumentException | UnsupportedOperationException exception) {
            failProgress(progressRegistry, telemetryId, safeMessage(exception));
            writeJson(exchange, mapper, 422, Map.of("error", "invalid_propagation_request", "detail", safeMessage(exception)));
        } catch (Exception exception) {
            failProgress(progressRegistry, telemetryId, safeMessage(exception));
            exception.printStackTrace(System.err);
            writeJson(exchange, mapper, 500, Map.of("error", "orekit_propagation_failed", "detail", safeMessage(exception)));
        }
    }

    private static void handlePropagationProgress(
            HttpExchange exchange,
            ObjectMapper mapper,
            PropagationProgressRegistry progressRegistry) throws IOException {
        if (!"GET".equals(exchange.getRequestMethod())) {
            writeJson(exchange, mapper, 405, Map.of("error", "method_not_allowed"));
            return;
        }
        String path = exchange.getRequestURI().getPath();
        String prefix = PROGRESS_PATH + "/";
        if (!path.startsWith(prefix) || path.length() <= prefix.length()) {
            writeJson(exchange, mapper, 400, Map.of("error", "progress_id_required"));
            return;
        }
        String telemetryId = path.substring(prefix.length());
        PropagationProgressRegistry.Snapshot snapshot = progressRegistry.get(telemetryId);
        if (snapshot == null) {
            writeJson(exchange, mapper, 404, Map.of("error", "progress_not_found"));
            return;
        }
        writeJson(exchange, mapper, 200, snapshot);
    }

    private static void handleOsculatingToMean(HttpExchange exchange, ObjectMapper mapper, MeanConversionEngine engine)
            throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            writeJson(exchange, mapper, 405, Map.of("error", "method_not_allowed"));
            return;
        }
        try {
            byte[] body = readBody(exchange);
            ApiModels.OsculatingToMeanRequest request = mapper.readValue(body, ApiModels.OsculatingToMeanRequest.class);
            writeJson(exchange, mapper, 200, engine.convert(request));
        } catch (RequestTooLargeException exception) {
            writeJson(exchange, mapper, 413, Map.of("error", "request_too_large"));
        } catch (IllegalArgumentException | UnsupportedOperationException exception) {
            writeJson(exchange, mapper, 422, Map.of("error", "invalid_mean_conversion_request", "detail", safeMessage(exception)));
        } catch (Exception exception) {
            exception.printStackTrace(System.err);
            writeJson(exchange, mapper, 500, Map.of("error", "orekit_mean_conversion_failed", "detail", safeMessage(exception)));
        }
    }

    private static void handleTleToMean(HttpExchange exchange, ObjectMapper mapper, TleMeanConversionEngine engine)
            throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            writeJson(exchange, mapper, 405, Map.of("error", "method_not_allowed"));
            return;
        }
        try {
            byte[] body = readBody(exchange);
            ApiModels.TleToMeanRequest request = mapper.readValue(body, ApiModels.TleToMeanRequest.class);
            writeJson(exchange, mapper, 200, engine.convert(request));
        } catch (RequestTooLargeException exception) {
            writeJson(exchange, mapper, 413, Map.of("error", "request_too_large"));
        } catch (IllegalArgumentException | UnsupportedOperationException exception) {
            writeJson(exchange, mapper, 422, Map.of("error", "invalid_tle_mean_conversion_request", "detail", safeMessage(exception)));
        } catch (Exception exception) {
            exception.printStackTrace(System.err);
            writeJson(exchange, mapper, 500, Map.of("error", "orekit_tle_mean_conversion_failed", "detail", safeMessage(exception)));
        }
    }

    private static void handleGpsAlmanacToMean(
            HttpExchange exchange,
            ObjectMapper mapper,
            GpsAlmanacMeanConversionEngine engine) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            writeJson(exchange, mapper, 405, Map.of("error", "method_not_allowed"));
            return;
        }
        try {
            byte[] body = readBody(exchange);
            ApiModels.GpsAlmanacToMeanRequest request = mapper.readValue(body, ApiModels.GpsAlmanacToMeanRequest.class);
            writeJson(exchange, mapper, 200, engine.convert(request));
        } catch (RequestTooLargeException exception) {
            writeJson(exchange, mapper, 413, Map.of("error", "request_too_large"));
        } catch (IllegalArgumentException | UnsupportedOperationException exception) {
            writeJson(exchange, mapper, 422, Map.of("error", "invalid_gps_almanac_mean_conversion_request", "detail", safeMessage(exception)));
        } catch (Exception exception) {
            exception.printStackTrace(System.err);
            writeJson(exchange, mapper, 500, Map.of("error", "orekit_gps_almanac_mean_conversion_failed", "detail", safeMessage(exception)));
        }
    }

    private static void handleGlonassAlmanacToMean(
            HttpExchange exchange,
            ObjectMapper mapper,
            GlonassAlmanacMeanConversionEngine engine) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            writeJson(exchange, mapper, 405, Map.of("error", "method_not_allowed"));
            return;
        }
        try {
            byte[] body = readBody(exchange);
            ApiModels.GlonassAlmanacToMeanRequest request = mapper.readValue(
                    body, ApiModels.GlonassAlmanacToMeanRequest.class);
            writeJson(exchange, mapper, 200, engine.convert(request));
        } catch (RequestTooLargeException exception) {
            writeJson(exchange, mapper, 413, Map.of("error", "request_too_large"));
        } catch (IllegalArgumentException | UnsupportedOperationException exception) {
            writeJson(exchange, mapper, 422, Map.of("error", "invalid_glonass_almanac_mean_conversion_request", "detail", safeMessage(exception)));
        } catch (Exception exception) {
            exception.printStackTrace(System.err);
            writeJson(exchange, mapper, 500, Map.of("error", "orekit_glonass_almanac_mean_conversion_failed", "detail", safeMessage(exception)));
        }
    }


    private static void handleRinexGlonassToMean(
            HttpExchange exchange,
            ObjectMapper mapper,
            RinexGlonassMeanConversionEngine engine) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            writeJson(exchange, mapper, 405, Map.of("error", "method_not_allowed"));
            return;
        }
        try {
            byte[] body = readBody(exchange);
            ApiModels.RinexGlonassToMeanRequest request = mapper.readValue(
                    body, ApiModels.RinexGlonassToMeanRequest.class);
            writeJson(exchange, mapper, 200, engine.convert(request));
        } catch (RequestTooLargeException exception) {
            writeJson(exchange, mapper, 413, Map.of("error", "request_too_large"));
        } catch (IllegalArgumentException | UnsupportedOperationException exception) {
            writeJson(exchange, mapper, 422, Map.of("error", "invalid_rinex_glonass_request", "detail", safeMessage(exception)));
        } catch (Exception exception) {
            exception.printStackTrace(System.err);
            writeJson(exchange, mapper, 500, Map.of("error", "orekit_rinex_glonass_failed", "detail", safeMessage(exception)));
        }
    }


    private static void handleRinexGnssToMean(
            HttpExchange exchange,
            ObjectMapper mapper,
            RinexGnssMeanConversionEngine engine) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            writeJson(exchange, mapper, 405, Map.of("error", "method_not_allowed"));
            return;
        }
        try {
            byte[] body = readBody(exchange);
            ApiModels.RinexGnssToMeanRequest request = mapper.readValue(
                    body, ApiModels.RinexGnssToMeanRequest.class);
            writeJson(exchange, mapper, 200, engine.convert(request));
        } catch (RequestTooLargeException exception) {
            writeJson(exchange, mapper, 413, Map.of("error", "request_too_large"));
        } catch (IllegalArgumentException | UnsupportedOperationException exception) {
            writeJson(exchange, mapper, 422, Map.of("error", "invalid_rinex_gnss_request", "detail", safeMessage(exception)));
        } catch (Exception exception) {
            exception.printStackTrace(System.err);
            writeJson(exchange, mapper, 500, Map.of("error", "orekit_rinex_gnss_failed", "detail", safeMessage(exception)));
        }
    }

    private static void failProgress(
            PropagationProgressRegistry progressRegistry,
            String telemetryId,
            String error) {
        if (telemetryId != null && !telemetryId.isBlank() && progressRegistry.get(telemetryId) != null) {
            progressRegistry.fail(telemetryId, error);
        }
    }

    private static byte[] readBody(HttpExchange exchange) throws IOException, RequestTooLargeException {
        byte[] body = exchange.getRequestBody().readNBytes(MAX_REQUEST_BYTES + 1);
        if (body.length > MAX_REQUEST_BYTES) {
            throw new RequestTooLargeException();
        }
        return body;
    }

    private static void writeJson(HttpExchange exchange, ObjectMapper mapper, int status, Object body)
            throws IOException {
        byte[] bytes = mapper.writeValueAsBytes(body);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.sendResponseHeaders(status, bytes.length);
        try (var output = exchange.getResponseBody()) {
            output.write(bytes);
        } finally {
            exchange.close();
        }
    }

    private static void writeStreamingJson(HttpExchange exchange, ObjectMapper mapper, int status, Object body)
            throws IOException {
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.sendResponseHeaders(status, 0);
        try (var output = exchange.getResponseBody()) {
            mapper.writeValue(output, body);
            output.flush();
        } finally {
            exchange.close();
        }
    }

    private static String safeMessage(Exception exception) {
        String message = exception.getMessage();
        return message == null || message.isBlank() ? exception.getClass().getSimpleName() : message;
    }

    private static String requireEnv(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException(name + " is required");
        }
        return value;
    }

    private static final class RequestTooLargeException extends Exception {}
}
