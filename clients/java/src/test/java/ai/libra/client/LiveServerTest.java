package ai.libra.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assume.assumeTrue;

import java.util.Map;

import org.junit.Test;

/**
 * Against a real Libra server, when LIBRA_TEST_URL (and LIBRA_TEST_TOKEN) are set; skipped otherwise.
 * CI starts `libra serve` for this.
 */
public class LiveServerTest {
    private static LibraClient client() {
        String url = System.getenv("LIBRA_TEST_URL");
        assumeTrue("set LIBRA_TEST_URL to run against a real server", url != null && !url.isEmpty());
        String token = System.getenv("LIBRA_TEST_TOKEN");
        return new LibraClient(url, token == null || token.isEmpty() ? null : token);
    }

    @Test
    public void realServer() throws Exception {
        LibraClient libra = client();
        Map<String, Object> info = libra.info();
        assertTrue(info.containsKey("skills"));
        assertTrue(libra.chat("what is (2+3)*4").trim().endsWith("20"));  // "(2+3)*4 = 20"
        assertTrue(libra.skills("").size() >= 10);

        Map<String, Object> job = libra.startJob("summarize \"One. Two. Three.\" in 2 sentences");
        Map<String, Object> done = libra.waitForJob((String) job.get("id"), 30_000);
        assertEquals("done", done.get("status"));
        @SuppressWarnings("unchecked")
        Map<String, Object> result = (Map<String, Object>) done.get("result");
        assertEquals(Boolean.TRUE, result.get("verified"));
        assertTrue(libra.jobs(5).size() >= 1);
    }
}
