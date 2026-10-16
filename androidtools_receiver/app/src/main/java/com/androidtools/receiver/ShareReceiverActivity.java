package com.androidtools.receiver;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/** Receives WhatsApp's exported content:// URI and stores it in Downloads. */
public final class ShareReceiverActivity extends Activity {
    private static final int REQUEST_WRITE_STORAGE = 42;
    private Uri pendingUri;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        pendingUri = extractUri(getIntent());
        if (pendingUri == null) {
            finish();
            return;
        }
        if (Build.VERSION.SDK_INT < 29
                && checkSelfPermission("android.permission.WRITE_EXTERNAL_STORAGE")
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.WRITE_EXTERNAL_STORAGE"}, REQUEST_WRITE_STORAGE);
        } else {
            receive(pendingUri);
        }
    }

    @Override
    public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request == REQUEST_WRITE_STORAGE && results.length > 0
                && results[0] == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            receive(pendingUri);
        } else {
            finish();
        }
    }

    private static Uri extractUri(Intent intent) {
        if (intent == null) return null;
        Uri uri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
        if (uri != null) return uri;
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (text != null) {
            try { return Uri.parse(text.toString()); } catch (Exception ignored) { }
        }
        return null;
    }

    private void receive(Uri source) {
        String name = safeName(displayName(source));
        if (name.length() == 0) name = "WhatsApp_Chat.txt";
        if (Build.VERSION.SDK_INT >= 29) {
            saveWithMediaStore(source, name);
        } else {
            saveLegacy(source, name);
        }
        finish();
    }

    private void saveWithMediaStore(Uri source, String name) {
        ContentResolver resolver = getContentResolver();
        ContentValues values = new ContentValues();
        values.put(MediaStore.Downloads.DISPLAY_NAME, uniqueName(name));
        values.put(MediaStore.Downloads.MIME_TYPE, mimeFor(name));
        values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/AndroidTools");
        values.put(MediaStore.Downloads.IS_PENDING, 1);
        Uri target = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
        if (target == null) return;
        try {
            copy(resolver, source, resolver.openOutputStream(target));
            ContentValues done = new ContentValues();
            done.put(MediaStore.Downloads.IS_PENDING, 0);
            resolver.update(target, done, null, null);
        } catch (Exception ignored) {
            resolver.delete(target, null, null);
        }
    }

    @SuppressWarnings("deprecation")
    private void saveLegacy(Uri source, String name) {
        File dir = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "AndroidTools");
        if (!dir.exists() && !dir.mkdirs()) return;
        try { copy(getContentResolver(), source, new FileOutputStream(new File(dir, uniqueName(name)))); }
        catch (Exception ignored) { }
    }

    private static void copy(ContentResolver resolver, Uri source, OutputStream out) throws Exception {
        if (out == null) throw new IllegalStateException("No se pudo abrir el destino");
        try (InputStream in = resolver.openInputStream(source); OutputStream target = out) {
            byte[] buffer = new byte[64 * 1024];
            int count;
            while ((count = in.read(buffer)) != -1) target.write(buffer, 0, count);
            target.flush();
        }
    }

    private String displayName(Uri uri) {
        try (Cursor c = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) return c.getString(0);
        } catch (Exception ignored) { }
        return "WhatsApp_Chat_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date()) + ".txt";
    }

    private String uniqueName(String name) {
        return name.replaceAll("[^A-Za-z0-9._ -]", "_");
    }

    private static String safeName(String name) { return name == null ? "" : name.trim(); }
    private static String mimeFor(String name) { return name.toLowerCase(Locale.US).endsWith(".zip") ? "application/zip" : "text/plain"; }
}
