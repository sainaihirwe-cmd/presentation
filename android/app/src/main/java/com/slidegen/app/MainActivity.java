package com.slidegen.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.MimeTypeMap;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;
import android.window.OnBackInvokedDispatcher;

import com.chaquo.python.PyException;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;

/**
 * SlideGen on the phone: the Python app runs as a server inside this app (127.0.0.1 only),
 * and a full-screen WebView shows it. No PC, IP address or network is needed to use it.
 */
public class MainActivity extends Activity {
    private static final int PICK_FILES = 1;
    private static final int ASK_STORAGE = 2;

    /** Port of the in-app server; kept while the app process lives so it starts only once. */
    private static int port = 0;

    private WebView web;
    private WebChromeClient chrome;
    private ValueCallback<Uri[]> fileCallback;
    private View fullscreenView;
    private WebChromeClient.CustomViewCallback fullscreenCallback;
    private String[] pendingDownload; // url, contentDisposition, mimeType (waiting for permission)

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        web = findViewById(R.id.web);
        setUpWebView();

        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::goBack);
        }

        if (port != 0) {
            open();
        } else {
            new Thread(this::startServer).start();
        }
    }

    private void startServer() {
        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(getApplicationContext()));
            }
            String dataDir = new File(getFilesDir(), "slidegen").getAbsolutePath();
            port = Python.getInstance().getModule("android_main").callAttr("start", dataDir).toInt();
            runOnUiThread(this::open);
        } catch (PyException e) {
            runOnUiThread(() -> {
                findViewById(R.id.spinner).setVisibility(View.GONE);
                ((TextView) findViewById(R.id.status)).setText("SlideGen could not start:\n" + e.getMessage());
            });
        }
    }

    private void open() {
        web.loadUrl("http://127.0.0.1:" + port + "/");
    }

    private boolean isOwnPage(Uri uri) {
        return "127.0.0.1".equals(uri.getHost());
    }

    private void setUpWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (isOwnPage(request.getUrl())) {
                    return false;
                }
                // YouTube, Wikipedia credits and other outside links open in the phone's browser
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl()));
                } catch (ActivityNotFoundException ignored) {
                }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                findViewById(R.id.splash).setVisibility(View.GONE);
                web.setVisibility(View.VISIBLE);
            }
        });

        chrome = new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (fileCallback != null) {
                    fileCallback.onReceiveValue(null);
                }
                fileCallback = callback;
                try {
                    startActivityForResult(pickerIntent(params), PICK_FILES);
                } catch (ActivityNotFoundException e) {
                    fileCallback = null;
                    toast("No app found to choose files.");
                    return false;
                }
                return true;
            }

            // full screen for the presenter and for videos
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (fullscreenView != null) {
                    callback.onCustomViewHidden();
                    return;
                }
                fullscreenView = view;
                fullscreenCallback = callback;
                ((FrameLayout) getWindow().getDecorView()).addView(view, new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
                setSystemBarsHidden(true);
            }

            @Override
            public void onHideCustomView() {
                if (fullscreenView == null) {
                    return;
                }
                ((FrameLayout) getWindow().getDecorView()).removeView(fullscreenView);
                fullscreenView = null;
                setSystemBarsHidden(false);
                fullscreenCallback.onCustomViewHidden();
                fullscreenCallback = null;
            }
        };
        web.setWebChromeClient(chrome);

        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) ->
                download(url, contentDisposition, mimeType));
    }

    /** A system file picker limited to the types the page's file input accepts. */
    private Intent pickerIntent(WebChromeClient.FileChooserParams params) {
        Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        List<String> mimes = new ArrayList<>();
        for (String accept : params.getAcceptTypes()) {
            for (String part : accept.split(",")) {
                String t = part.trim().toLowerCase();
                if (t.startsWith(".")) {
                    t = MimeTypeMap.getSingleton().getMimeTypeFromExtension(t.substring(1));
                    if (t == null) { // an extension Android doesn't know: allow every file
                        mimes.clear();
                        break;
                    }
                }
                if (!t.isEmpty() && !mimes.contains(t)) {
                    mimes.add(t);
                }
            }
        }
        if (!mimes.isEmpty()) {
            intent.putExtra(Intent.EXTRA_MIME_TYPES, mimes.toArray(new String[0]));
        }
        if (params.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE) {
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        }
        return intent;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != PICK_FILES || fileCallback == null) {
            super.onActivityResult(requestCode, resultCode, data);
            return;
        }
        Uri[] result = null;
        if (resultCode == RESULT_OK && data != null) {
            if (data.getClipData() != null) {
                result = new Uri[data.getClipData().getItemCount()];
                for (int i = 0; i < result.length; i++) {
                    result[i] = data.getClipData().getItemAt(i).getUri();
                }
            } else if (data.getData() != null) {
                result = new Uri[]{data.getData()};
            }
        }
        fileCallback.onReceiveValue(result);
        fileCallback = null;
    }

    // ------------------------------------------------------------- downloads (.pptx)

    private void download(String url, String contentDisposition, String mimeType) {
        if (Build.VERSION.SDK_INT < 29 && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            pendingDownload = new String[]{url, contentDisposition, mimeType};
            requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, ASK_STORAGE);
            return;
        }
        toast("Preparing your PowerPoint…");
        new Thread(() -> save(url, contentDisposition, mimeType)).start();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        if (requestCode == ASK_STORAGE && pendingDownload != null) {
            String[] d = pendingDownload;
            pendingDownload = null;
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
                download(d[0], d[1], d[2]);
            } else {
                toast("SlideGen needs storage permission to save the file.");
            }
        }
    }

    private void save(String url, String contentDisposition, String mimeType) {
        String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
        Uri saved = null;
        try {
            HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
            try (InputStream in = conn.getInputStream()) {
                if (Build.VERSION.SDK_INT >= 29) {
                    ContentValues values = new ContentValues();
                    values.put(MediaStore.Downloads.DISPLAY_NAME, name);
                    values.put(MediaStore.Downloads.MIME_TYPE, mimeType);
                    values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
                    saved = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                    try (OutputStream out = getContentResolver().openOutputStream(saved)) {
                        copy(in, out);
                    }
                } else {
                    File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
                    dir.mkdirs();
                    try (OutputStream out = new FileOutputStream(new File(dir, name))) {
                        copy(in, out);
                    }
                }
            } finally {
                conn.disconnect();
            }
        } catch (Exception e) {
            runOnUiThread(() -> toast("Could not save the file: " + e.getMessage()));
            return;
        }
        Uri file = saved;
        runOnUiThread(() -> showSaved(name, file, mimeType));
    }

    private static void copy(InputStream in, OutputStream out) throws java.io.IOException {
        byte[] buf = new byte[64 * 1024];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
        }
    }

    private void showSaved(String name, Uri file, String mimeType) {
        AlertDialog.Builder dialog = new AlertDialog.Builder(this)
                .setTitle("Saved to Downloads")
                .setMessage(name)
                .setNegativeButton("Close", null);
        if (file != null) {
            dialog.setPositiveButton("Open", (d, w) -> launch(new Intent(Intent.ACTION_VIEW)
                    .setDataAndType(file, mimeType), "Install PowerPoint or Google Slides to open it."));
            dialog.setNeutralButton("Share", (d, w) -> launch(Intent.createChooser(new Intent(Intent.ACTION_SEND)
                    .setType(mimeType).putExtra(Intent.EXTRA_STREAM, file), name), null));
        }
        dialog.show();
    }

    private void launch(Intent intent, String missingAppMessage) {
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            startActivity(intent);
        } catch (ActivityNotFoundException e) {
            if (missingAppMessage != null) {
                toast(missingAppMessage);
            }
        }
    }

    // --------------------------------------------------------------- navigation

    private void goBack() {
        if (fullscreenView != null) {
            chrome.onHideCustomView();
        } else if (web.canGoBack()) {
            web.goBack();
        } else {
            finish();
        }
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() { // Android 12 and older
        goBack();
    }

    @SuppressWarnings("deprecation")
    private void setSystemBarsHidden(boolean hidden) {
        View decor = getWindow().getDecorView();
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController c = decor.getWindowInsetsController();
            if (c == null) {
                return;
            }
            if (hidden) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            } else {
                c.show(WindowInsets.Type.systemBars());
            }
        } else {
            decor.setSystemUiVisibility(hidden
                    ? View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                      | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    : 0);
        }
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
    }

    @Override
    protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }
}
