package ai.libra.client;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A client for a Libra server's API, with no dependencies.
 *
 * <pre>{@code
 * LibraClient libra = new LibraClient(PairLink.parse("libra://pair?base=…&token=dev_…", null));
 * String answer = libra.chat("what is (2+3)*4");
 * Map<String, Object> job = libra.startJob("scrape https://example.com/news then summarize it in 3 sentences");
 * }</pre>
 *
 * Methods block, so call them off any UI thread. The token is only ever sent to the server it
 * belongs to: redirects are not followed.
 */
public final class LibraClient {
    private static final int MAX_BODY = 4_000_000;

    private final PairLink link;
    private int connectTimeoutMs = 10_000;
    private int readTimeoutMs = 120_000;
    private String userAgent = "libra-client-java/" + version();

    public LibraClient(PairLink link) {
        if (link == null) throw new IllegalArgumentException("link is null");
        this.link = link;
    }

    /** {@code address} like {@code https://atlas.tail1234.ts.net} or {@code 192.168.1.20:8000}. */
    public LibraClient(String address, String token) {
        this(PairLink.of(address, token));
    }

    public LibraClient timeouts(int connectMs, int readMs) {
        this.connectTimeoutMs = connectMs;
        this.readTimeoutMs = readMs;
        return this;
    }

    /** Added to the User-Agent header, e.g. "NexusAgent/2.1". */
    public LibraClient userAgent(String product) {
        this.userAgent = product + " " + userAgent;
        return this;
    }

    public PairLink link() {
        return link;
    }

    // ------------------------------------------------------------------ server
    /** {@code GET /v1/info}: the server's name, whether it has a model, files, skills and training. */
    public Map<String, Object> info() {
        return object(request("GET", "/v1/info", null));
    }

    /** True if the server answers and accepts this sign-in. */
    public boolean ping() {
        try {
            info();
            return true;
        } catch (LibraException e) {
            return false;
        }
    }

    // -------------------------------------------------------------------- chat
    /** One question, one answer. Skills are tried first (calculations, the time, scraping, …). */
    public String chat(String message) {
        return chat(Collections.singletonList(message(Role.USER, message)));
    }

    /** A conversation; each message is {@code {"role": "user"|"assistant"|"system", "content": …}}. */
    public String chat(List<Map<String, Object>> messages) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("messages", messages);
        body.put("libra_auto_skills", Boolean.TRUE);
        Map<String, Object> out = object(request("POST", "/v1/chat/completions", body));
        List<Object> choices = list(out.get("choices"));
        if (choices.isEmpty()) throw new LibraException(200, "the server returned no answer");
        return String.valueOf(object(object(choices.get(0)).get("message")).get("content"));
    }

    public enum Role {
        USER("user"), ASSISTANT("assistant"), SYSTEM("system");

        final String wire;

        Role(String wire) {
            this.wire = wire;
        }
    }

    public static Map<String, Object> message(Role role, String content) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("role", role.wire);
        m.put("content", content);
        return m;
    }

    // --------------------------------------------------------------- language
    /** Translate {@code text} into a language name or code ("Spanish", "es"). Returns the full result. */
    public Map<String, Object> translate(String text, String to) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("text", text);
        body.put("to", to);
        return object(request("POST", "/v1/translate", body));
    }

    // ------------------------------------------------------------------ skills
    /** {@code GET /v1/skills?q=…}: skills matching the words (all built-ins if {@code query} is empty). */
    public List<Object> skills(String query) {
        String path = "/v1/skills" + (query == null || query.isEmpty() ? "" : "?q=" + encode(query));
        return list(object(request("GET", path, null)).get("data"));
    }

    /**
     * Runs one skill. Skills that change something follow the server's trust rules: if one needs
     * approval, this throws a {@link LibraException} with status 403. Returns {@code skill, ok, text, data}.
     */
    public Map<String, Object> runSkill(String name, Map<String, Object> arguments) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("arguments", arguments == null ? new LinkedHashMap<>() : arguments);
        return object(request("POST", "/v1/skills/" + encode(name), body));
    }

    // -------------------------------------------------------------------- jobs
    /** Starts a durable job for a plain-language goal. Returns the job (with its {@code id}). */
    public Map<String, Object> startJob(String goal) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("goal", goal);
        return object(object(request("POST", "/v1/jobs", body)).get("job"));
    }

    /** The most recent jobs: {@code id, name, goal, status, running, waiting, steps_done, steps}. */
    public List<Object> jobs(int n) {
        return list(object(request("GET", "/v1/jobs?n=" + Math.max(1, Math.min(n, 200)), null)).get("jobs"));
    }

    /** One job with its steps, result ({@code output, checks, verified, sha256}) and recent events. */
    public Map<String, Object> job(String id) {
        return object(request("GET", "/v1/jobs/" + jobId(id), null));
    }

    /** Lets the step a job is waiting on run, once. */
    public Map<String, Object> approve(String id) {
        return jobAction(id, "approve");
    }

    /** Skips the step a job is waiting on. */
    public Map<String, Object> skip(String id) {
        return jobAction(id, "skip");
    }

    public Map<String, Object> cancel(String id) {
        return jobAction(id, "cancel");
    }

    /**
     * Polls until the job is done, failed, cancelled, needs review, or waiting for you, or until
     * {@code timeoutMs} passes. Returns the job as last seen.
     */
    public Map<String, Object> waitForJob(String id, long timeoutMs) throws InterruptedException {
        long end = System.currentTimeMillis() + timeoutMs;
        while (true) {
            Map<String, Object> j = job(id);
            String status = String.valueOf(j.get("status"));
            boolean running = Boolean.TRUE.equals(j.get("running"));
            if (!running && !status.equals("queued") && !status.equals("running")) return j;
            if (System.currentTimeMillis() > end) return j;
            Thread.sleep(500);
        }
    }

    private Map<String, Object> jobAction(String id, String action) {
        return object(object(request("POST", "/v1/jobs/" + jobId(id) + "/" + action, new LinkedHashMap<>())).get("job"));
    }

    private static String jobId(String id) {
        if (id == null || !id.matches("[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")) {
            throw new IllegalArgumentException("not a job id: " + id);
        }
        return id;
    }

    // ---------------------------------------------------------------- training
    /** {@code GET /v1/auto/status}: whether training runs, its progress, and {@code last_run.decision}. */
    public Map<String, Object> trainingStatus() {
        return object(request("GET", "/v1/auto/status", null));
    }

    // --------------------------------------------------------------- transport
    private Object request(String method, String path, Object body) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(link.base + path).openConnection();
            c.setRequestMethod(method);
            c.setConnectTimeout(connectTimeoutMs);
            c.setReadTimeout(readTimeoutMs);
            c.setInstanceFollowRedirects(false);
            c.setRequestProperty("Accept", "application/json");
            c.setRequestProperty("User-Agent", userAgent);
            if (link.token != null) c.setRequestProperty("Authorization", "Bearer " + link.token);
            if (body != null) {
                byte[] data = Json.write(body).getBytes(StandardCharsets.UTF_8);
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setFixedLengthStreamingMode(data.length);
                try (OutputStream out = c.getOutputStream()) {
                    out.write(data);
                }
            }
            int status = c.getResponseCode();
            InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
            String text = in == null ? "" : read(in);
            if (status >= 300) throw new LibraException(status, describe(status, text));
            try {
                return Json.parse(text);
            } catch (IllegalArgumentException e) {
                throw new LibraException(status, "the server's reply wasn't JSON: " + e.getMessage());
            }
        } catch (IOException e) {
            throw new LibraException("couldn't reach " + link.base + ": " + e.getMessage(), e);
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private static String describe(int status, String body) {
        String msg = null;
        try {
            Map<String, Object> reply = object(Json.parse(body));
            Object err = reply.get("error");
            if (err instanceof Map) msg = String.valueOf(((Map<?, ?>) err).get("message"));
            else if (reply.get("text") != null) msg = String.valueOf(reply.get("text"));  // a refused skill
        } catch (RuntimeException ignored) {
            // not JSON
        }
        if (status == 401) return "the server didn't accept this token (wrong or revoked)";
        if (status >= 300 && status < 400) return "the server answered with a redirect (" + status + "); use its final address";
        return "HTTP " + status + (msg == null || msg.equals("null") ? "" : ": " + msg);
    }

    private static String read(InputStream in) throws IOException {
        try (InputStream s = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = s.read(buf)) > 0) {
                out.write(buf, 0, n);
                if (out.size() > MAX_BODY) throw new IOException("reply too large");
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object o) {
        if (o instanceof Map) return (Map<String, Object>) o;
        throw new LibraException(200, "unexpected reply from the server");
    }

    @SuppressWarnings("unchecked")
    private static List<Object> list(Object o) {
        if (o instanceof List) return (List<Object>) o;
        if (o == null) return new ArrayList<>();
        throw new LibraException(200, "unexpected reply from the server");
    }

    private static String encode(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8").replace("+", "%20");
        } catch (java.io.UnsupportedEncodingException e) {
            throw new IllegalStateException(e);
        }
    }

    /** This library's version (from the jar's manifest, or "dev" when run from source). */
    public static String version() {
        String v = LibraClient.class.getPackage() == null ? null : LibraClient.class.getPackage().getImplementationVersion();
        return v == null ? "dev" : v;
    }
}
