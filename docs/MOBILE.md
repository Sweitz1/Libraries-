# Libra on your phone

There are two ways to use Libra from a phone. Both talk to **your own Libra server**; nothing goes through anyone else's service.

| | Android app | Browser (any phone, including iPhone) |
|---|---|---|
| Install | the APK from CI ([below](#install-the-android-app)) | open the address, then *Add to Home screen* |
| Sign-in | scan `libra pair --app`; the token is encrypted with the Android Keystore | scan `libra pair`; the token is kept in the browser's storage |
| Notifications | yes: jobs waiting for approval, finished goals, training decisions, even with the app closed | ntfy, or the web app's own notifications while it's open (HTTPS only) |
| Works offline | shows an offline screen with *Try again* | the browser's error page |

The app's screens are the same web app the server serves at `/`, so every feature arrives on the phone as soon as the server is updated, without a new APK.

## Install the Android app

1. On GitHub, open **Actions → android**, pick the latest green run, and download the **libra-android** artifact (a zip with `libra.apk`).
   - For a published GitHub release, `libra.apk` is also attached to the release.
2. Copy `libra.apk` to the phone and open it. Android asks you to allow installing from that source (Files, Chrome or Drive) once.
3. Requires Android 8.0 or newer.

Without a release key, each CI build signs the APK with a new temporary debug key. Android installs it fine, but a later APK can't update it: you'd have to uninstall first, and that loses the app's sign-in. To sign properly, create a keystore once:

```bash
keytool -genkeypair -v -keystore libra-release.jks -alias libra -keyalg RSA -keysize 4096 -validity 10000
base64 -w0 libra-release.jks   # paste into the repository secret LIBRA_KEYSTORE_BASE64
```

Then add the secrets `LIBRA_KEYSTORE_PASSWORD`, `LIBRA_KEY_ALIAS` (`libra`) and `LIBRA_KEY_PASSWORD`. Keep the `.jks` file safe: you need the same key for every future update. To re-sign a release that's already published, re-run its workflow (Actions → release → the tag's run → *Re-run all jobs*); the files are replaced.

## Pair it

On the server:

```bash
libra pair --app --name "my phone"
```

Scan the code with the phone's camera (or Google Lens). It opens the Libra app with the address filled in. Check that it is your server, then tap **Connect**. Other ways:

- **From the web app:** Pair → tick *For the Libra Android app* → New QR code.
- **From a phone browser** already signed in: tap *Open it there* in the banner.
- **Type it:** the address (`https://atlas.tail1234.ts.net` or `192.168.1.20:8000`) plus a token from `libra devices add "my phone"`.

Each phone gets its own token. A lost phone is cut off with `libra devices revoke <id>`, or from the Pair screen. Signing out in the app also deletes its saved sign-in.

**Use the Tailscale address when you can.** It is `https://` and works away from home. On plain `http://` (home Wi-Fi), someone on the same network could read the token, and the app asks you to confirm before connecting.

## Notifications

The first time the app opens, Android asks whether it may send notifications. About every 15 minutes, while there's a network, the app checks your server and tells you when:

- a **job is waiting for you**: a step needs approval, a change was interrupted, or a change would act on content from the web;
- a **goal finished**: done, failed, or needs a look;
- a **training run decided**: kept the new model, or kept the old one and why.

Tapping a notification opens the right tab. The check only reads; approving is always something you do in the app. Fifteen minutes is the shortest interval Android allows for background checks. Battery savers can make it less frequent.

## How it is built

`android/` is a small Java app that uses only the Android framework (no libraries), like the rest of Libra:

| File | What it does |
|---|---|
| [`PairLink.java`](../android/app/src/main/java/ai/libra/app/PairLink.java) | Reads a sign-in from a `libra://pair` link, a browser link, or a typed address. Unit-tested |
| [`PairActivity.java`](../android/app/src/main/java/ai/libra/app/PairActivity.java) | The connect screen. A scanned link only fills the form, so a stray QR code can't silently point the app at another server |
| [`TokenStore.java`](../android/app/src/main/java/ai/libra/app/TokenStore.java) | The token is encrypted with an AES-GCM key that stays inside the Android Keystore; backups are off |
| [`MainActivity.java`](../android/app/src/main/java/ai/libra/app/MainActivity.java) | The web app in a locked-down WebView: no JavaScript bridge, no file access, no device permissions for pages, other sites open in the browser |
| [`CheckService.java`](../android/app/src/main/java/ai/libra/app/CheckService.java), [`Updates.java`](../android/app/src/main/java/ai/libra/app/Updates.java), [`Notifier.java`](../android/app/src/main/java/ai/libra/app/Notifier.java) | The background check and what it notifies about (each event once; nothing old after installing). Unit-tested |

CI ([`.github/workflows/android.yml`](../.github/workflows/android.yml)) runs the unit tests and Android lint, then builds the debug and release APKs.

## The contract an app relies on (for iOS later)

An iPhone app would be the same thin shell in Swift: a `WKWebView`, the token in the iOS Keychain, and `BGAppRefreshTask` for checks. It needs nothing new from the server. Everything a native app uses:

| What | How |
|---|---|
| Pairing link | `libra://pair?base=<url-encoded https://host[:port]>&token=<device token>` (`libra pair --app`, or `app_url` from `POST /v1/pair`) |
| Browser link | `https://host[:port]/#token=<token>[&tab=<tab>]`. The fragment is never sent to the server, and the web app stores the token and removes it from the address bar |
| Check a sign-in | `GET /v1/info` with `Authorization: Bearer <token>`: 200 with `auth` and `skills` keys means Libra; 401 means wrong or revoked |
| Jobs | `GET /v1/jobs?n=20` → `jobs[]` with `id`, `name`, `goal`, `status` (`queued`, `running`, `waiting`, `done`, `failed`, `needs_review`, `cancelled`), `running`, and `waiting.reason` |
| Training | `GET /v1/auto/status` → `last_run.finished` and `last_run.decision` (404 when the server doesn't train) |
| Open a tab | `#tab=chat\|translate\|skills\|agent\|training` |
| Tell the app the page is inside it | add `LibraAndroid/<version>` (or `LibraiOS/<version>`) to the user agent; the web app then sends sign-out to `libra://signout` |
| Revoke | `POST /v1/devices/revoke {"id": …}` (the app's own device, or devices it paired) |

Building for iPhone needs a Mac with Xcode. Putting it on a phone needs an Apple developer account: free for your own phone for 7 days at a time, or $99/year for TestFlight and longer installs. Until then, the web app works in Safari: Share → *Add to Home Screen*.

## Not verified yet

The APK is built and lint-checked in CI, and the parsing and notification logic is unit-tested. It has **not** yet been run on a real phone or an emulator. Please report anything odd: pairing, the WebView, notifications on your Android version, or battery-saver behaviour.
