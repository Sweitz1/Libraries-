package ai.libra.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.List;

import org.junit.Test;

public class UpdatesTest {
    private static Updates.Job job(String id, String status, boolean running) {
        return new Updates.Job(id, "morning", "collect the sources", status, running,
                status.equals("waiting") ? "step 3 (send_report) needs your approval" : "");
    }

    private static List<Updates.Note> check(Updates.State[] state, String decision, Updates.Job... jobs) {
        Updates.State next = new Updates.State();
        String key = decision.isEmpty() ? "" : "2026-11-21T03:00:00+00:00 " + decision.length();
        List<Updates.Note> notes = Updates.diff(state[0], Arrays.asList(jobs), key, decision, next);
        state[0] = Updates.State.decode(next.encode());  // stored between checks as text
        return notes;
    }

    @Test
    public void firstCheckIsQuietThenEachEventOnce() {
        Updates.State[] s = {new Updates.State()};
        assertTrue(check(s, "kept the old model", job("a", "done", false), job("b", "waiting", false)).isEmpty());

        List<Updates.Note> n = check(s, "kept the old model", job("a", "done", false), job("b", "waiting", false),
                job("c", "running", true));
        assertTrue(n.isEmpty());  // nothing new: b was already waiting

        n = check(s, "kept the new model (held-out loss did not get worse)", job("b", "running", true),
                job("c", "waiting", false));
        assertEquals(2, n.size());
        assertEquals("wait-c", n.get(0).key);
        assertEquals("step 3 (send_report) needs your approval", n.get(0).text);
        assertEquals("training", n.get(1).key);
        assertEquals("training", n.get(1).tab);
        assertEquals("kept the new model (held-out loss did not get worse)", n.get(1).text);

        n = check(s, "kept the new model (held-out loss did not get worse)", job("b", "done", false),
                job("c", "waiting", false));
        assertEquals(1, n.size());
        assertEquals("end-b", n.get(0).key);
        assertEquals("Goal finished: morning", n.get(0).title);

        n = check(s, "kept the new model (held-out loss did not get worse)", job("b", "done", false),
                job("c", "running", true));
        assertTrue(n.isEmpty());
        n = check(s, "kept the new model (held-out loss did not get worse)", job("c", "waiting", false));
        assertEquals("wait-c", n.get(0).key);  // a second wait on the same job is a new event
    }

    @Test
    public void jobsThatStartAndEndBetweenChecksAreReported() {
        Updates.State[] s = {new Updates.State()};
        check(s, "");
        List<Updates.Note> n = check(s, "", job("d", "failed", false), job("e", "needs_review", false));
        assertEquals(2, n.size());
        assertEquals("Goal failed: morning", n.get(0).title);
        assertEquals("Goal finished, but needs a look: morning", n.get(1).title);
    }
}
