package ai.libra.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;

/** Android notifications for what {@link Updates} found. Tapping one opens the right tab. */
final class Notifier {
    static final String CHANNEL = "libra-updates";

    private Notifier() {
    }

    static void ensureChannel(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null || nm.getNotificationChannel(CHANNEL) != null) return;
        NotificationChannel ch = new NotificationChannel(CHANNEL, "Goals and training",
                NotificationManager.IMPORTANCE_DEFAULT);
        ch.setDescription("When a goal needs your approval or finishes, and when training decides on a model");
        nm.createNotificationChannel(ch);
    }

    static void show(Context c, Updates.Note note) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null || !nm.areNotificationsEnabled()) return;
        ensureChannel(c);
        Intent open = new Intent(c, MainActivity.class)
                .putExtra(MainActivity.EXTRA_TAB, note.tab)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pi = PendingIntent.getActivity(c, note.key.hashCode(), open,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n = new Notification.Builder(c, CHANNEL)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(note.title)
                .setContentText(note.text)
                .setStyle(new Notification.BigTextStyle().bigText(note.text))
                .setContentIntent(pi)
                .setAutoCancel(true)
                .build();
        nm.notify(note.key.hashCode(), n);
    }
}
