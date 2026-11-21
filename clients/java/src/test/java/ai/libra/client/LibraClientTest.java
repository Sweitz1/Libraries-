package ai.libra.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;

/** The client against a small fake server (the real one is tested in LiveServerTest). */
public class LibraClientTest {
    private HttpServer server;
    private final List<String> seen = Collections.synchronizedList(new ArrayList<>());
    private String base;

    @Before
    public void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            String auth = ex.getRequestHeaders().getFirst("Authorization");
            seen.add(ex.getRequestMethod() + " " + ex.getRequestURI() + " " + auth + " " + body);
            String path = ex.getRequestURI().getPath();
            int status = 200;
            String reply;
            if (!"Bearer dev_goodtoken".equals(auth)) {
                status = 401;
                reply = "{\"error\":{\"message\":\"missing or wrong token\"}}";
            } else if (path.equals("/v1/info")) {
                reply = "{\"name\":\"libra\",\"auth\":true,\"skills\":12}";
            } else if (path.equals("/v1/chat/completions")) {
                reply = "{\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"20\"}}]}";
            } else if (path.equals("/v1/skills/wipe")) {
                status = 403;
                reply = "{\"skill\":\"wipe\",\"ok\":false,\"text\":\"'wipe' is a danger skill and needs your approval\"}";
            } else if (path.equals("/v1/jobs")) {
                reply = "{\"started\":true,\"job\":{\"id\":\"20261121-090000-abcdef\",\"status\":\"queued\"}}";
            } else if (path.equals("/v1/jobs/20261121-090000-abcdef")) {
                reply = "{\"id\":\"20261121-090000-abcdef\",\"status\":\"done\",\"running\":false}";
            } else if (path.equals("/v1/moved")) {
                ex.getResponseHeaders().add("Location", "http://evil.example/steal");
                status = 302;
                reply = "{}";
            } else {
                status = 404;
                reply = "{\"error\":{\"message\":\"not found\"}}";
            }
            byte[] out = reply.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(status, out.length);
            try (OutputStream os = ex.getResponseBody()) {
                os.write(out);
            }
        });
        server.start();
        base = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @After
    public void stop() {
        server.stop(0);
    }

    @Test
    public void chatSkillsAndJobs() throws Exception {
        LibraClient libra = new LibraClient(base, "dev_goodtoken");
        assertEquals("libra", libra.info().get("name"));
        assertTrue(libra.ping());
        assertEquals("20", libra.chat("what is (2+3)*4"));
        assertTrue(seen.get(seen.size() - 1).contains("\"libra_auto_skills\":true"));
        assertTrue(seen.get(seen.size() - 1).contains("\"content\":\"what is (2+3)*4\""));

        Map<String, Object> job = libra.startJob("collect the sources");
        assertEquals("20261121-090000-abcdef", job.get("id"));
        assertEquals("done", libra.waitForJob((String) job.get("id"), 5000).get("status"));
        try {
            libra.job("../../etc/passwd");
            fail("accepted a path as a job id");
        } catch (IllegalArgumentException expected) {
            // fine
        }
    }

    @Test
    public void errorsAreClear() {
        LibraClient libra = new LibraClient(base, "dev_goodtoken");
        try {
            libra.runSkill("wipe", null);
            fail();
        } catch (LibraException e) {
            assertEquals(403, e.status());
            assertTrue(e.getMessage().contains("needs your approval"));
            assertFalse(e.isTemporary());
        }
        try {
            new LibraClient(base, "dev_wrongtoken").info();
            fail();
        } catch (LibraException e) {
            assertEquals(401, e.status());
            assertTrue(e.getMessage().contains("wrong or revoked"));
        }
        assertFalse(new LibraClient(base, "dev_wrongtoken").ping());
        try {
            new LibraClient("http://127.0.0.1:1", null).timeouts(500, 500).info();
            fail();
        } catch (LibraException e) {
            assertEquals(0, e.status());
            assertTrue(e.isTemporary());
        }
    }

    @Test
    public void neverFollowsRedirectsWithTheToken() throws Exception {
        java.lang.reflect.Method request = LibraClient.class.getDeclaredMethod("request", String.class, String.class,
                Object.class);
        request.setAccessible(true);
        try {
            request.invoke(new LibraClient(base, "dev_goodtoken"), "GET", "/v1/moved", null);
            fail();
        } catch (java.lang.reflect.InvocationTargetException e) {
            assertEquals(302, ((LibraException) e.getCause()).status());
        }
        for (String s : seen) assertFalse(s, s.contains("evil.example"));
    }

    @Test
    public void pairingLinksWork() {
        LibraClient libra = new LibraClient(PairLink.parse(
                "libra://pair?base=" + base.replace(":", "%3A").replace("/", "%2F") + "&token=dev_goodtoken", null));
        assertEquals("libra", libra.info().get("name"));
    }
}
