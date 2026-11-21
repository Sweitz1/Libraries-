package ai.libra.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.junit.Test;

public class PairLinkTest {
    @Test
    public void appLinkFromLibraPairDashApp() {
        PairLink p = PairLink.parse("libra://pair?base=https%3A%2F%2Fatlas.tail1234.ts.net&token=dev_abcDEF123_-x", null);
        assertEquals("https://atlas.tail1234.ts.net", p.base);
        assertEquals("dev_abcDEF123_-x", p.token);
        assertTrue(p.isSecure());
        assertEquals("https://atlas.tail1234.ts.net/#token=dev_abcDEF123_-x&tab=agent", p.appUrl("agent"));
    }

    @Test
    public void browserLinkAndSharedText() {
        PairLink p = PairLink.parse("Open Libra: http://192.168.1.20:8000/#token=dev_12345678", null);
        assertEquals("http://192.168.1.20:8000", p.base);
        assertEquals("dev_12345678", p.token);
        assertFalse(p.isSecure());
        assertEquals("http://192.168.1.20:8000/", PairLink.parse("http://192.168.1.20:8000/", null).appUrl(null));
    }

    @Test
    public void bareAddressWithTypedToken() {
        PairLink p = PairLink.parse("  Atlas.local:8000/some/path ", " dev_typedtoken ");
        assertEquals("http://atlas.local:8000", p.base);
        assertEquals("dev_typedtoken", p.token);
        assertNull(PairLink.parse("10.0.0.5:8000", "").token);
        assertEquals("http://[fe80::1]:8000", PairLink.parse("http://[fe80::1]:8000", null).base);
    }

    @Test
    public void rejectsWhatIsNotASignIn() {
        for (String bad : new String[] {"", "ftp://x.example", "libra://other?base=x", "libra://pair?token=dev_12345678",
                "http://user:pw@x.example", "javascript:alert(1)"}) {
            try {
                PairLink.parse(bad, null);
                fail("accepted " + bad);
            } catch (IllegalArgumentException expected) {
                // fine
            }
        }
        try {
            PairLink.parse("https://x.example/#token=bad token<script>", null);
            fail("accepted a malformed token");
        } catch (IllegalArgumentException expected) {
            // fine
        }
    }
}
