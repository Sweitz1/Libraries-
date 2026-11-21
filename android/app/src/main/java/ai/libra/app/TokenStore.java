package ai.libra.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * The sign-in (server address and device token), stored in the app's private preferences with
 * the token encrypted by a key that never leaves the Android Keystore. The token can't be read
 * from a backup or a copied data folder. Backups are switched off as well.
 */
final class TokenStore {
    private static final String PREFS = "libra";
    private static final String ALIAS = "libra-sign-in";

    private TokenStore() {
    }

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static SecretKey key() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        if (ks.containsAlias(ALIAS)) return ((KeyStore.SecretKeyEntry) ks.getEntry(ALIAS, null)).getSecretKey();
        KeyGenerator gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        gen.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return gen.generateKey();
    }

    static void save(Context c, PairLink link) throws Exception {
        SharedPreferences.Editor e = prefs(c).edit().clear().putString("base", link.base);
        if (link.token != null) {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            byte[] sealed = cipher.doFinal(link.token.getBytes(StandardCharsets.UTF_8));
            e.putString("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                    .putString("token", Base64.encodeToString(sealed, Base64.NO_WRAP));
        }
        if (!e.commit()) throw new IllegalStateException("couldn't save the sign-in");
    }

    /** The saved sign-in, or null if there is none (or it can no longer be decrypted). */
    static PairLink load(Context c) {
        SharedPreferences p = prefs(c);
        String base = p.getString("base", null);
        if (base == null) return null;
        String token = null;
        String sealed = p.getString("token", null);
        if (sealed != null) {
            try {
                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                byte[] iv = Base64.decode(p.getString("iv", ""), Base64.NO_WRAP);
                cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, iv));
                token = new String(cipher.doFinal(Base64.decode(sealed, Base64.NO_WRAP)), StandardCharsets.UTF_8);
            } catch (Exception e) {
                return null;  // e.g. the keystore was reset: pair again
            }
        }
        try {
            return PairLink.parse(token == null ? base : base + "/#token=" + token, null);
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    static void clear(Context c) {
        prefs(c).edit().clear().commit();
    }

    /** What the last background check saw (see {@link Updates.State}). */
    static String updates(Context c) {
        return prefs(c).getString("updates", "");
    }

    static void setUpdates(Context c, String state) {
        prefs(c).edit().putString("updates", state).apply();
    }

    static boolean flag(Context c, String name) {
        return prefs(c).getBoolean(name, false);
    }

    static void setFlag(Context c, String name, boolean value) {
        prefs(c).edit().putBoolean(name, value).apply();
    }
}
