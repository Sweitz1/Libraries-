package ai.libra.app;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Checks the server about every 15 minutes (the shortest period Android allows) while there's a
 * network, and notifies about jobs waiting for you, finished goals, and training decisions.
 * It only reads: it never approves or changes anything.
 */
public class CheckService extends JobService {
    static final int JOB_ID = 1;

    static void schedule(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        if (js == null) return;
        for (JobInfo j : js.getAllPendingJobs()) if (j.getId() == JOB_ID) return;
        js.schedule(new JobInfo.Builder(JOB_ID, new ComponentName(c, CheckService.class))
                .setPeriodic(15 * 60 * 1000L)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPersisted(true)
                .build());
    }

    static void cancel(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        if (js != null) js.cancel(JOB_ID);
    }

    @Override
    public boolean onStartJob(JobParameters params) {
        new Thread(() -> {
            try {
                check(getApplicationContext());
            } catch (Exception ignored) {
                // offline, server down, or not reachable from here: try again next time
            }
            jobFinished(params, false);
        }, "libra-check").start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;
    }

    static void check(Context c) throws Exception {
        PairLink link = TokenStore.load(c);
        if (link == null) return;
        Api.Response r = Api.get(link, "/v1/jobs?n=20");
        if (r.status == 401) {  // revoked or replaced: say so once, not every 15 minutes
            if (!TokenStore.flag(c, "told-signed-out")) {
                TokenStore.setFlag(c, "told-signed-out", true);
                Notifier.show(c, new Updates.Note("signed-out", "Libra signed this phone out",
                        "Its sign-in was revoked or no longer works. Open Libra to pair again.", "chat"));
            }
            return;
        }
        TokenStore.setFlag(c, "told-signed-out", false);
        List<Updates.Job> jobs = new ArrayList<>();
        if (r.status == 200) {
            JSONArray arr = new JSONObject(r.body).optJSONArray("jobs");
            for (int i = 0; arr != null && i < arr.length(); i++) {
                JSONObject j = arr.getJSONObject(i);
                JSONObject waiting = j.optJSONObject("waiting");
                jobs.add(new Updates.Job(j.optString("id"), j.optString("name"), j.optString("goal"),
                        j.optString("status"), j.optBoolean("running"),
                        waiting == null ? "" : waiting.optString("reason")));
            }
        }
        String runKey = "", decision = "";
        Api.Response a = Api.get(link, "/v1/auto/status");  // 404 when the server doesn't train
        if (a.status == 200) {
            JSONObject last = new JSONObject(a.body).optJSONObject("last_run");
            if (last != null) {
                runKey = last.optString("finished");
                decision = last.optString("decision");
            }
        }
        Updates.State prev = Updates.State.decode(TokenStore.updates(c));
        Updates.State next = new Updates.State();
        List<Updates.Note> notes = Updates.diff(prev, jobs, runKey, decision, next);
        TokenStore.setUpdates(c, next.encode());
        for (Updates.Note n : notes) Notifier.show(c, n);
    }
}
