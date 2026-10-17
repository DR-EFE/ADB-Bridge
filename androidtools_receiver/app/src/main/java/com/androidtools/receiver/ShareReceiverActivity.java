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
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Receives WhatsApp content and publishes an ADB-readable receipt. */
public final class ShareReceiverActivity extends Activity {
    private static final int REQUEST_WRITE_STORAGE = 42;
    private static final String DIRECTORY = "AndroidTools";
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private Uri pendingUri;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        pendingUri = extractUri(getIntent());
        if (pendingUri == null) { finish(); return; }
        if (Build.VERSION.SDK_INT < 29 && checkSelfPermission("android.permission.WRITE_EXTERNAL_STORAGE")
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.WRITE_EXTERNAL_STORAGE"}, REQUEST_WRITE_STORAGE);
        } else receiveAsync(pendingUri);
    }

    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request == REQUEST_WRITE_STORAGE && results.length > 0
                && results[0] == android.content.pm.PackageManager.PERMISSION_GRANTED) receiveAsync(pendingUri);
        else finish();
    }

    private void receiveAsync(final Uri source) {
        worker.execute(new Runnable() {
            @Override public void run() {
                String id = UUID.randomUUID().toString();
                long createdAt = System.currentTimeMillis();
                String requestedName = safeName(displayName(source));
                if (requestedName.length() == 0) requestedName = fallbackName();
                String outputName = uniqueName(requestedName, id);
                try {
                    CopyResult result = savePayload(source, outputName);
                    publishReceipt(id, "READY", outputName, mimeFor(outputName), result.bytes,
                            result.sha256, createdAt, "");
                } catch (Exception error) {
                    try {
                        publishReceipt(id, "FAILED", outputName, mimeFor(outputName), 0, "", createdAt,
                                error.getClass().getSimpleName() + ": " + safeError(error.getMessage()));
                    } catch (Exception ignored) { }
                } finally {
                    runOnUiThread(new Runnable() { @Override public void run() { finish(); } });
                }
            }
        });
    }

    private static Uri extractUri(Intent intent) {
        if (intent == null) return null;
        Uri uri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
        if (uri != null) return uri;
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (text != null) try { return Uri.parse(text.toString()); } catch (Exception ignored) { }
        return null;
    }

    private CopyResult savePayload(Uri source, String name) throws Exception {
        if (Build.VERSION.SDK_INT >= 29) return saveWithMediaStore(source, name, mimeFor(name));
        return saveLegacy(source, name);
    }

    private CopyResult saveWithMediaStore(Uri source, String name, String mime) throws Exception {
        ContentValues values = new ContentValues();
        values.put(MediaStore.Downloads.DISPLAY_NAME, name);
        values.put(MediaStore.Downloads.MIME_TYPE, mime);
        values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/" + DIRECTORY);
        values.put(MediaStore.Downloads.IS_PENDING, 1);
        ContentResolver resolver = getContentResolver();
        Uri target = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
        if (target == null) throw new IllegalStateException("No se pudo crear el destino");
        try {
            CopyResult result = copy(resolver, source, resolver.openOutputStream(target));
            ContentValues done = new ContentValues();
            done.put(MediaStore.Downloads.IS_PENDING, 0);
            resolver.update(target, done, null, null);
            return result;
        } catch (Exception error) { resolver.delete(target, null, null); throw error; }
    }

    @SuppressWarnings("deprecation")
    private CopyResult saveLegacy(Uri source, String name) throws Exception {
        File dir = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), DIRECTORY);
        if (!dir.exists() && !dir.mkdirs()) throw new IllegalStateException("No se pudo crear Download/AndroidTools");
        File partial = new File(dir, "." + name + ".partial");
        File target = new File(dir, name);
        try {
            CopyResult result = copy(getContentResolver(), source, new FileOutputStream(partial));
            if (!partial.renameTo(target)) throw new IllegalStateException("No se pudo publicar el archivo");
            return result;
        } finally { if (partial.exists()) partial.delete(); }
    }

    private void publishReceipt(String id, String state, String fileName, String mime, long bytes,
                                String sha256, long createdAt, String error) throws Exception {
        String receiptName = "AndroidToolsReceipt_" + id + ".json";
        String remotePath = "/sdcard/Download/" + DIRECTORY + "/" + fileName;
        String json = "{\"id\":\"" + esc(id) + "\",\"state\":\"" + esc(state)
                + "\",\"fileName\":\"" + esc(fileName) + "\",\"remotePath\":\"" + esc(remotePath)
                + "\",\"mimeType\":\"" + esc(mime) + "\",\"sizeBytes\":" + bytes
                + ",\"sha256\":\"" + esc(sha256) + "\",\"createdAtEpochMs\":" + createdAt
                + ",\"error\":\"" + esc(error) + "\"}";
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues values = new ContentValues();
            values.put(MediaStore.Downloads.DISPLAY_NAME, receiptName);
            values.put(MediaStore.Downloads.MIME_TYPE, "application/json");
            values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/" + DIRECTORY);
            values.put(MediaStore.Downloads.IS_PENDING, 1);
            ContentResolver resolver = getContentResolver();
            Uri target = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
            if (target == null) throw new IllegalStateException("No se pudo crear comprobante");
            try (OutputStream out = resolver.openOutputStream(target)) {
                if (out == null) throw new IllegalStateException("No se pudo abrir comprobante");
                out.write(json.getBytes("UTF-8")); out.flush();
                ContentValues done = new ContentValues(); done.put(MediaStore.Downloads.IS_PENDING, 0);
                resolver.update(target, done, null, null);
            } catch (Exception errorWrite) { resolver.delete(target, null, null); throw errorWrite; }
        } else {
            File dir = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), DIRECTORY);
            if (!dir.exists() && !dir.mkdirs()) throw new IllegalStateException("No se pudo crear carpeta de comprobantes");
            File partial = new File(dir, "." + receiptName + ".partial");
            File target = new File(dir, receiptName);
            try (OutputStream out = new FileOutputStream(partial)) { out.write(json.getBytes("UTF-8")); out.flush(); }
            if (!partial.renameTo(target)) throw new IllegalStateException("No se pudo publicar comprobante");
        }
    }

    private static CopyResult copy(ContentResolver resolver, Uri source, OutputStream out) throws Exception {
        if (out == null) throw new IllegalStateException("No se pudo abrir el destino");
        MessageDigest digest = MessageDigest.getInstance("SHA-256"); long bytes = 0;
        try (InputStream in = resolver.openInputStream(source); OutputStream target = out) {
            if (in == null) throw new IllegalStateException("No se pudo abrir el archivo compartido");
            byte[] buffer = new byte[64 * 1024]; int count;
            while ((count = in.read(buffer)) != -1) { target.write(buffer, 0, count); digest.update(buffer, 0, count); bytes += count; }
            target.flush();
        }
        return new CopyResult(bytes, hex(digest.digest()));
    }

    private String displayName(Uri uri) {
        try (Cursor c = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) return c.getString(0);
        } catch (Exception ignored) { }
        return fallbackName();
    }
    private static String fallbackName() { return "WhatsApp_Chat_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date()) + ".txt"; }
    private static String uniqueName(String name, String id) { int dot = name.lastIndexOf('.'); String base = dot > 0 ? name.substring(0, dot) : name; String ext = dot > 0 ? name.substring(dot) : ".txt"; return base.replaceAll("[^A-Za-z0-9._ -]", "_") + "_" + id.substring(0, 8) + ext.replaceAll("[^A-Za-z0-9.]", ""); }
    private static String mimeFor(String name) { return name.toLowerCase(Locale.US).endsWith(".zip") ? "application/zip" : "text/plain"; }
    private static String safeName(String name) { return name == null ? "" : name.trim(); }
    private static String safeError(String text) { return text == null ? "" : text; }
    private static String esc(String text) { return (text == null ? "" : text).replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r"); }
    private static String hex(byte[] data) { StringBuilder out = new StringBuilder(data.length * 2); for (byte b : data) out.append(String.format(Locale.US, "%02x", b & 0xff)); return out.toString(); }
    private static final class CopyResult { final long bytes; final String sha256; CopyResult(long bytes, String sha256) { this.bytes = bytes; this.sha256 = sha256; } }
}
