package ai.libra.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * Connects the app to a Libra server. A link from a QR code or a share only fills in the form:
 * the person sees the address and taps Connect, so a stray code can't silently point the app at
 * someone else's server.
 */
public class PairActivity extends Activity {
    private EditText address;
    private EditText token;
    private TextView status;
    private Button connect;
    private final Handler main = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setTitle("Connect to Libra");
        int pad = dp(24);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(pad, dp(40), pad, pad);

        TextView title = text("Connect to your Libra", 24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        col.addView(title);
        col.addView(text("On the computer running Libra, run\n\n    libra pair --app\n\nand scan the code with this "
                + "phone's camera. Or paste a pairing link, or type the server's address and a token from "
                + "`libra devices add`.", 15));

        address = new EditText(this);
        address.setHint("https://atlas.tail1234.ts.net, 192.168.1.20:8000 or a link");
        address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        address.setSingleLine(false);
        col.addView(address, matchWidth(dp(12)));

        token = new EditText(this);
        token.setHint("token (only if the link has none)");
        token.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        col.addView(token, matchWidth(dp(8)));

        connect = new Button(this);
        connect.setText("Connect");
        connect.setOnClickListener(v -> start());
        col.addView(connect, matchWidth(dp(16)));

        status = text("", 15);
        col.addView(status, matchWidth(dp(12)));

        ScrollView scroll = new ScrollView(this);
        scroll.addView(col);
        setContentView(scroll);
        fill(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        fill(intent);
    }

    /** A link that arrived from a QR code or a share fills the form; nothing connects until Connect. */
    private void fill(Intent intent) {
        if (intent == null) return;
        String text = null;
        if (Intent.ACTION_VIEW.equals(intent.getAction())) {
            Uri data = intent.getData();
            if (data != null) text = data.toString();
        } else if (Intent.ACTION_SEND.equals(intent.getAction())) {
            text = intent.getStringExtra(Intent.EXTRA_TEXT);
        }
        if (text == null) return;
        try {
            PairLink p = PairLink.parse(text, null);
            address.setText(text);
            status.setText("Connect to " + p.base + "?" + (p.isSecure() ? "" : "\n\n" + INSECURE)
                    + "\n\nOnly continue if this is your own Libra server.");
        } catch (IllegalArgumentException e) {
            status.setText(e.getMessage());
        }
    }

    private static final String INSECURE = "This address isn't encrypted (http), so someone on the same Wi-Fi "
            + "could read your sign-in. Libra's Tailscale address (https://….ts.net) is safer.";

    private void start() {
        final PairLink p;
        try {
            p = PairLink.parse(address.getText().toString(), token.getText().toString());
        } catch (IllegalArgumentException e) {
            status.setText(e.getMessage());
            return;
        }
        if (p.isSecure()) {
            check(p);
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("Not encrypted")
                .setMessage(INSECURE + "\n\nUse it anyway (for example, at home only)?")
                .setPositiveButton("Use it", (d, w) -> check(p))
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void check(PairLink p) {
        connect.setEnabled(false);
        status.setText("Connecting to " + p.base + "…");
        new Thread(() -> {
            String problem = null;
            try {
                Api.Response r = Api.get(p, "/v1/info");
                if (r.status == 401) {
                    problem = "The server didn't accept that token. Make a new code with `libra pair --app`.";
                } else if (r.status == 429) {
                    problem = "Too many wrong tokens from this phone. Wait a few minutes.";
                } else if (r.status != 200) {
                    problem = "The server answered " + r.status + ". Is this Libra's address?";
                } else {
                    JSONObject info = new JSONObject(r.body);
                    if (!info.has("auth") || !info.has("skills")) {
                        problem = "That address answered, but it isn't a Libra server.";
                    } else if (info.optBoolean("auth") && p.token == null) {
                        problem = "This server needs a token. Scan a code from `libra pair --app`.";
                    } else {
                        TokenStore.save(this, p);
                        TokenStore.setUpdates(this, "");
                    }
                }
            } catch (Exception e) {
                problem = "Couldn't reach " + p.base + " (" + e.getClass().getSimpleName() + ": " + e.getMessage()
                        + "). Is the phone on the same network, or Tailscale on?";
            }
            final String msg = problem;
            main.post(() -> {
                connect.setEnabled(true);
                if (msg != null) {
                    status.setText(msg);
                    return;
                }
                startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP));
                finish();
            });
        }, "libra-pair").start();
    }

    private TextView text(String s, int sp) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setPadding(0, dp(8), 0, dp(8));
        t.setGravity(Gravity.START);
        t.setTextIsSelectable(true);
        return t;
    }

    private LinearLayout.LayoutParams matchWidth(int top) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = top;
        return lp;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
