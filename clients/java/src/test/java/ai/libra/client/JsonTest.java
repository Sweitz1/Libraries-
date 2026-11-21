package ai.libra.client;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.fail;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.Test;

public class JsonTest {
    @Test
    @SuppressWarnings("unchecked")
    public void parsesWhatTheServerSends() {
        Map<String, Object> m = (Map<String, Object>) Json.parse(
                "{\"a\": [1, -2.5, 3e2, true, false, null], \"s\": \"caf\\u00e9 \\\"q\\\" \\n\", \"big\": 12345678901234567890,"
                        + " \"o\": {}, \"l\": []}");
        List<Object> a = (List<Object>) m.get("a");
        assertEquals(Arrays.asList(1L, -2.5, 300.0, true, false, null), a);
        assertEquals("café \"q\" \n", m.get("s"));
        assertEquals(1.2345678901234567e19, (Double) m.get("big"), 1e4);
        assertEquals(new LinkedHashMap<>(), m.get("o"));
        assertNull(Json.parse(" null "));
    }

    @Test
    public void writesAndRoundTrips() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("text", "line\none \"two\" \\ \u0001");
        m.put("n", 3);
        m.put("x", 2.5);
        m.put("list", Arrays.asList("a", null, true));
        String out = Json.write(m);
        assertEquals("{\"text\":\"line\\none \\\"two\\\" \\\\ \\u0001\",\"n\":3,\"x\":2.5,\"list\":[\"a\",null,true]}", out);
        assertEquals(Json.write(Json.parse(out)), out.replace("\"n\":3", "\"n\":3"));
    }

    @Test
    public void rejectsBrokenJson() {
        for (String bad : new String[] {"", "{", "[1,]", "{\"a\" 1}", "\"open", "tru", "1 2", "{\"a\":\"\u0001\"}"}) {
            try {
                Json.parse(bad);
                fail("accepted " + bad);
            } catch (IllegalArgumentException expected) {
                // fine
            }
        }
        StringBuilder deep = new StringBuilder();
        for (int i = 0; i < 500; i++) deep.append('[');
        try {
            Json.parse(deep.toString());
            fail("accepted 500 levels of nesting");
        } catch (IllegalArgumentException expected) {
            // fine: no stack overflow
        }
    }
}
