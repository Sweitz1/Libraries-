package ai.libra.app;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Decides what to notify about, from the server's job list and training status, compared with
 * what the last check saw. Plain Java, so it is unit-tested on the JVM.
 *
 * <ul>
 *   <li>A job waiting for you (an approval, an interrupted change, outside content): once per wait.</li>
 *   <li>A job that finished (done, failed, needs review): once.</li>
 *   <li>A new training decision ("kept the new model …"): once.</li>
 * </ul>
 * The first check only records what is there, so installing the app doesn't replay old history.
 */
public final class Updates {
    public static final class Job {
        final String id, name, goal, status, reason;
        final boolean running;

        public Job(String id, String name, String goal, String status, boolean running, String reason) {
            this.id = id;
            this.name = name == null ? "" : name;
            this.goal = goal == null ? "" : goal;
            this.status = status == null ? "" : status;
            this.running = running;
            this.reason = reason == null ? "" : reason;
        }

        String label() {
            String s = name.isEmpty() ? goal : name;
            return s.length() > 60 ? s.substring(0, 57) + "…" : s;
        }
    }

    public static final class Note {
        public final String key, title, text, tab;

        Note(String key, String title, String text, String tab) {
            this.key = key;
            this.title = title;
            this.text = text;
            this.tab = tab;
        }
    }

    /** What the previous check saw. */
    public static final class State {
        boolean started;
        final Map<String, String> statuses = new LinkedHashMap<>();
        final Set<String> waitingNotified = new LinkedHashSet<>();
        String decision = "";  // the latest training run seen (its key)

        public String encode() {
            StringBuilder b = new StringBuilder(started ? "1" : "0").append('\n');
            for (Map.Entry<String, String> e : statuses.entrySet()) b.append(e.getKey()).append('=').append(e.getValue()).append(';');
            b.append('\n');
            for (String w : waitingNotified) b.append(w).append(';');
            return b.append('\n').append(decision.replace('\n', ' ')).toString();
        }

        public static State decode(String s) {
            State st = new State();
            if (s == null || s.isEmpty()) return st;
            String[] lines = s.split("\n", -1);
            st.started = lines[0].equals("1");
            if (lines.length > 1) {
                for (String kv : lines[1].split(";")) {
                    int eq = kv.indexOf('=');
                    if (eq > 0) st.statuses.put(kv.substring(0, eq), kv.substring(eq + 1));
                }
            }
            if (lines.length > 2) {
                for (String w : lines[2].split(";")) if (!w.isEmpty()) st.waitingNotified.add(w);
            }
            if (lines.length > 3) st.decision = lines[3];
            return st;
        }
    }

    static boolean isFinal(String status) {
        return status.equals("done") || status.equals("failed") || status.equals("needs_review");
    }

    /**
     * Compares; fills {@code next} with what this check saw; returns what to notify about.
     * {@code runKey} identifies the latest training run (its finish time), {@code decision} is its result.
     */
    public static List<Note> diff(State prev, List<Job> jobs, String runKey, String decision, State next) {
        List<Note> notes = new ArrayList<>();
        for (Job j : jobs) {
            next.statuses.put(j.id, j.status);
            boolean waiting = j.status.equals("waiting") && !j.running;
            if (waiting) {
                next.waitingNotified.add(j.id);
                if (prev.started && !prev.waitingNotified.contains(j.id)) {
                    notes.add(new Note("wait-" + j.id, "Libra is waiting for you: " + j.label(),
                            j.reason.isEmpty() ? "A step needs your approval." : j.reason, "agent"));
                }
            }
            String before = prev.statuses.get(j.id);
            boolean wasOpen = before == null ? prev.started : !isFinal(before);
            if (isFinal(j.status) && wasOpen && !j.running) {
                String what = j.status.equals("done") ? "finished" : j.status.equals("failed") ? "failed"
                        : "finished, but needs a look";
                notes.add(new Note("end-" + j.id, "Goal " + what + ": " + j.label(),
                        j.status.equals("done") ? "The result passed its checks." : "Open Libra to see what happened.",
                        "agent"));
            }
        }
        next.decision = runKey == null ? "" : runKey;
        if (prev.started && runKey != null && !runKey.isEmpty() && !runKey.equals(prev.decision)) {
            notes.add(new Note("training", "Libra training finished",
                    decision == null || decision.isEmpty() ? "A training run finished." : decision, "training"));
        }
        next.started = true;
        return notes;
    }

    private Updates() {
    }
}
