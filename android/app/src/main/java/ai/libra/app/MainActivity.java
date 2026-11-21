package ai.libra.app;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * The Libra web app, served by your own server, in a locked-down WebView. Pages from other
 * addresses open in the browser instead. There is no JavaScript bridge, so a page can't reach
 * into the app. The only messages from the page are the {@code libra://} links it navigates to.
 */
public class MainActivity extends Activity {
    static final String EXTRA_TAB = "tab";

    private PairLink link;
    private WebView web;
    private LinearLayout offline;
    private TextView offlineText;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        link = TokenStore.load(this);
        if (link == null) {
            startActivity(new Intent(this, PairActivity.class));
            finish();
            return;
        }
        FrameLayout root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        offline = buildOffline();
        root.addView(offline);
        setContentView(root);
        setUpWebView();
        Notifier.ensureChannel(this);
        askForNotifications();
        CheckService.schedule(this);
        if (saved == null || web.restoreState(saved) == null) {
            web.loadUrl(link.appUrl(getIntent().getStringExtra(EXTRA_TAB)));
        }
    }

    private void setUpWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);  // the app is a JavaScript page
        s.setDomStorageEnabled(true);  // the web app's small preferences (its sign-in is an HttpOnly cookie)
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setSupportMultipleWindows(false);
        s.setGeolocationEnabled(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setUserAgentString(s.getUserAgentString() + " LibraAndroid/" + BuildConfig.VERSION_NAME);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return handle(request.getUrl());
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                offline.setVisibility(View.GONE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showOffline(String.valueOf(error.getDescription()));
            }
        });
        web.setWebChromeClient(new WebChromeClient() {  // also shows the page's confirm() dialogs
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                request.deny();  // no camera, microphone or other device access for pages
            }
        });
    }

    /** True if the app handled the address itself (so the WebView doesn't load it). */
    private boolean handle(Uri uri) {
        String scheme = uri.getScheme() == null ? "" : uri.getScheme();
        if (scheme.equals("libra")) {
            if ("signout".equals(uri.getHost())) signOut();
            else if ("pair".equals(uri.getHost())) startActivity(new Intent(Intent.ACTION_VIEW, uri, this, PairActivity.class));
            return true;
        }
        if (sameServer(uri)) return false;
        try {  // anything else (a link in a chat answer, a web page) opens outside the app
            startActivity(new Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE));
        } catch (ActivityNotFoundException ignored) {
            // nothing can open it
        }
        return true;
    }

    private boolean sameServer(Uri uri) {
        Uri base = Uri.parse(link.base);
        return base.getScheme().equals(uri.getScheme()) && base.getHost() != null
                && base.getHost().equalsIgnoreCase(uri.getHost()) && base.getPort() == uri.getPort();
    }

    private void signOut() {
        TokenStore.clear(this);
        CheckService.cancel(this);
        WebStorage.getInstance().deleteAllData();
        CookieManager.getInstance().removeAllCookies(null);
        web.clearCache(true);
        startActivity(new Intent(this, PairActivity.class));
        finish();
    }

    private LinearLayout buildOffline() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        int pad = Math.round(24 * getResources().getDisplayMetrics().density);
        box.setPadding(pad, pad, pad, pad);
        box.setBackgroundColor(getColor(R.color.bg));
        offlineText = new TextView(this);
        offlineText.setTextSize(16);
        offlineText.setTextColor(getColor(R.color.text));
        offlineText.setGravity(Gravity.CENTER);
        box.addView(offlineText);
        Button retry = new Button(this);
        retry.setText("Try again");
        retry.setOnClickListener(v -> {
            offline.setVisibility(View.GONE);
            web.loadUrl(link.appUrl(null));
        });
        box.addView(retry);
        Button change = new Button(this);
        change.setText("Use a different server");
        change.setOnClickListener(v -> signOut());
        box.addView(change);
        box.setVisibility(View.GONE);
        return box;
    }

    private void showOffline(String why) {
        offlineText.setText("Can't reach Libra at " + link.base + ".\n\n" + why
                + "\n\nIs the server running, and is this phone on the same network (or on Tailscale)?");
        offline.setVisibility(View.VISIBLE);
    }

    private void askForNotifications() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                && !TokenStore.flag(this, "asked-notifications")) {
            TokenStore.setFlag(this, "asked-notifications", true);
            requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        String tab = intent.getStringExtra(EXTRA_TAB);
        if (tab != null && web != null) web.loadUrl(link.appUrl(tab));
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        if (web != null) web.saveState(out);
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {  // back goes back within the app first (the predictive-back opt-in is off)
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
