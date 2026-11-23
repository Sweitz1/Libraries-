# Libra client for Java

A Java client for a [Libra](../../README.md) server, with **no dependencies**: a small JSON reader and writer is built in. It works from Java 11 and on Android, so Java apps (for example Nexus Agent) can use your Libra.

```java
import ai.libra.client.LibraClient;
import ai.libra.client.PairLink;

// From a pairing code (`libra pair --app`), a browser link, or an address plus a device token
LibraClient libra = new LibraClient(PairLink.parse("libra://pair?base=https%3A%2F%2Fatlas.tail1234.ts.net&token=dev_…", null));
// or: new LibraClient("https://atlas.tail1234.ts.net", "dev_…")

libra.chat("what is (2+3)*4");                       // "(2+3)*4 = 20": skills first, then your files or the model
libra.translate("Where is the hotel?", "Spanish");   // {"text": …, …}
libra.runSkill("summarize", Map.of("text", longText, "sentences", 3));

Map<String, Object> job = libra.startJob("scrape https://example.com/news then summarize it in 3 sentences");
Map<String, Object> done = libra.waitForJob((String) job.get("id"), 60_000);
// status: done | failed | needs_review | waiting (then libra.approve(id) or libra.skip(id)) | cancelled
```

Everything else: `info()`, `ping()`, `skills(query)`, `jobs(n)`, `job(id)`, `cancel(id)`, `trainingStatus()`. Errors throw `LibraException`, whose `status()` is the HTTP code: 401 is a wrong or revoked token, 403 a skill that needs approval. `isTemporary()` is true for busy, rate-limited, or unreachable.

The methods block, so call them off the UI thread. The token is only sent to its own server: redirects are never followed. Get a token for an app with `libra devices add "my app"`, and revoke it with `libra devices revoke <id>`.

## Install from GitHub Packages

Each Libra release publishes `ai.libra:libra-client` to GitHub Packages. GitHub requires a sign-in even to download a public package, so you need a **personal access token (classic)** with the `read:packages` scope: GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic).

**Maven.** In `~/.m2/settings.xml`:

```xml
<settings>
  <servers>
    <server>
      <id>github</id>
      <username>YOUR_GITHUB_USERNAME</username>
      <password>YOUR_TOKEN</password>
    </server>
  </servers>
</settings>
```

and in your `pom.xml`:

```xml
<repositories>
  <repository>
    <id>github</id>
    <url>https://maven.pkg.github.com/sweitz1/Libraries-</url>
  </repository>
</repositories>

<dependencies>
  <dependency>
    <groupId>ai.libra</groupId>
    <artifactId>libra-client</artifactId>
    <version>0.19.1</version>
  </dependency>
</dependencies>
```

**Gradle (Android too):**

```groovy
repositories {
    maven {
        url "https://maven.pkg.github.com/sweitz1/Libraries-"
        credentials {
            username = System.getenv("GITHUB_ACTOR")   // or your username
            password = System.getenv("GITHUB_TOKEN")   // a token with read:packages
        }
    }
}
dependencies {
    implementation "ai.libra:libra-client:0.19.1"
}
```

**Without GitHub Packages:** each [release](https://github.com/Sweitz1/Libraries-/releases) has `libra-client-<version>.jar` attached, listed in its `SHA256SUMS`. It's a single jar with nothing else needed.

## Build and test

```bash
cd clients/java
mvn verify                                   # unit tests, plus a fake-server test
LIBRA_TEST_URL=http://127.0.0.1:8000 LIBRA_TEST_TOKEN=… mvn verify   # also against a running Libra
```

CI runs both on every push: it starts `libra serve` and runs the live test against it. Pushing a release tag also runs `mvn deploy` to GitHub Packages, with the workflow's own `GITHUB_TOKEN` (see [`release.yml`](../../.github/workflows/release.yml)).
