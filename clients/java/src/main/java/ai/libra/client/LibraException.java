package ai.libra.client;

/** The server refused or failed a request. {@link #status()} is the HTTP status (0 if it never answered). */
public class LibraException extends RuntimeException {
    private static final long serialVersionUID = 1L;
    private final int status;

    public LibraException(int status, String message) {
        super(message);
        this.status = status;
    }

    public LibraException(String message, Throwable cause) {
        super(message, cause);
        this.status = 0;
    }

    /** 401: wrong or revoked token. 403: a skill needs approval or is denied. 429/503: busy, try later. */
    public int status() {
        return status;
    }

    /** True if trying again later may work (rate limit, busy, or the server couldn't be reached). */
    public boolean isTemporary() {
        return status == 0 || status == 429 || status == 503;
    }
}
