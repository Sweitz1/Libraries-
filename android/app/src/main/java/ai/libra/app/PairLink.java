package ai.libra.app;

import java.net.URI;
import java.net.URISyntaxException;
import java.io.UnsupportedEncodingException;
import java.net.URLDecoder;
import java.util.Locale;

/**
 * Reads a sign-in from what the person scanned, shared or typed. Plain Java (no Android classes),
 * so it is unit-tested on the JVM. Accepted forms:
 * <ul>
 *   <li>{@code libra://pair?base=https%3A%2F%2Fatlas.tail1234.ts.net&token=dev_…} (from {@code libra pair --app})</li>
 *   <li>{@code https://atlas.tail1234.ts.net/#token=dev_…} (the browser link from {@code libra pair})</li>
 *   <li>an address on its own, such as {@code 192.168.1.20:8000} or {@code atlas.local:8000}, plus a token typed separately</li>
 * </ul>
 */
public final class PairLink {
    public final String base;   // scheme://host[:port], no trailing slash
    public final String token;  // may be null (servers started with --no-auth)

    private PairLink(String base, String token) {
        this.base = base;
        this.token = token;
    }

    /** Parses {@code text}; {@code typedToken} is used when the text carries no token. */
    public static PairLink parse(String text, String typedToken) throws IllegalArgumentException {
        String s = text == null ? "" : text.trim();
        if (s.isEmpty()) throw new IllegalArgumentException("Enter the server's address or paste a pairing link.");
        int space = s.indexOf(' ');  // shared text can be "Open Libra: https://…"
        if (space >= 0 && !s.startsWith("libra:")) {
            for (String part : s.split("\\s+")) {
                if (part.startsWith("http://") || part.startsWith("https://") || part.startsWith("libra://")) {
                    s = part;
                    break;
                }
            }
        }
        String base;
        String token = null;
        if (s.startsWith("libra://")) {
            URI u = uri(s);
            if (!"pair".equals(u.getHost())) throw new IllegalArgumentException("That isn't a Libra pairing link.");
            base = query(u.getRawQuery(), "base");
            token = query(u.getRawQuery(), "token");
            if (base == null) throw new IllegalArgumentException("The pairing link has no server address.");
        } else {
            if (!s.contains("://")) s = "http://" + s;  // a bare address on the home network
            int hash = s.indexOf('#');
            if (hash >= 0) {
                token = query(s.substring(hash + 1), "token");
                s = s.substring(0, hash);
            }
            base = s;
        }
        base = normalise(base);
        if (token == null || token.isEmpty()) {
            token = typedToken == null || typedToken.trim().isEmpty() ? null : typedToken.trim();
        }
        if (token != null && !token.matches("[A-Za-z0-9_\\-.~+/=]{8,200}")) {
            throw new IllegalArgumentException("That token doesn't look right. Copy it again from `libra devices add`.");
        }
        return new PairLink(base, token);
    }

    /** Checks the scheme and host, and drops any path: the app always lives at the server's root. */
    static String normalise(String base) {
        URI u = uri(base.trim());
        String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
        if (!scheme.equals("http") && !scheme.equals("https")) {
            throw new IllegalArgumentException("The address must start with http:// or https://");
        }
        String host = u.getHost();
        if (host == null || host.isEmpty()) throw new IllegalArgumentException("The address has no host name.");
        if (u.getRawUserInfo() != null) throw new IllegalArgumentException("Leave the user name out of the address.");
        String h = host.contains(":") && !host.startsWith("[") ? "[" + host + "]" : host;
        return scheme + "://" + h.toLowerCase(Locale.ROOT) + (u.getPort() > 0 ? ":" + u.getPort() : "");
    }

    /** True if the connection is encrypted (https), so the token can't be read on the network. */
    public boolean isSecure() {
        return base.startsWith("https://");
    }

    /** The web app's address with the sign-in in the fragment, which browsers never send to the server. */
    public String appUrl(String tab) {
        StringBuilder b = new StringBuilder(base).append('/');
        String frag = "";
        if (token != null) frag = "token=" + token;
        if (tab != null && tab.matches("[a-z]{1,20}")) frag = frag.isEmpty() ? "tab=" + tab : frag + "&tab=" + tab;
        return frag.isEmpty() ? b.toString() : b.append('#').append(frag).toString();
    }

    private static URI uri(String s) {
        try {
            return new URI(s);
        } catch (URISyntaxException e) {
            throw new IllegalArgumentException("That address isn't valid.");
        }
    }

    private static String query(String raw, String key) {
        if (raw == null) return null;
        for (String pair : raw.split("&")) {
            int eq = pair.indexOf('=');
            if (eq > 0 && pair.substring(0, eq).equals(key)) {
                try {
                    return URLDecoder.decode(pair.substring(eq + 1), "UTF-8");  // the Charset overload needs API 33
                } catch (UnsupportedEncodingException | IllegalArgumentException e) {
                    throw new IllegalArgumentException("The pairing link is damaged.");
                }
            }
        }
        return null;
    }
}
