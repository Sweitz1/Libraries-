package ai.libra.app;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Small blocking HTTP client for the Libra API. Never call it on the main thread. */
final class Api {
    static final int MAX_BODY = 2_000_000;

    static final class Response {
        final int status;
        final String body;

        Response(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    private Api() {
    }

    static Response get(PairLink link, String path) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(link.base + path).openConnection();
        try {
            c.setConnectTimeout(10_000);
            c.setReadTimeout(20_000);
            c.setInstanceFollowRedirects(false);  // never carry the token to another address
            c.setRequestProperty("Accept", "application/json");
            c.setRequestProperty("User-Agent", "LibraAndroid/" + BuildConfig.VERSION_NAME);
            if (link.token != null) c.setRequestProperty("Authorization", "Bearer " + link.token);
            int status = c.getResponseCode();
            InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
            return new Response(status, in == null ? "" : read(in));
        } finally {
            c.disconnect();
        }
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
            return out.toString(StandardCharsets.UTF_8.name());
        }
    }
}
