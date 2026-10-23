package com.roni.hisab;

import android.view.View;
import android.webkit.WebView;
import com.getcapacitor.BridgeActivity;

/*
 * The fingerprint prompt runs in its own Activity. When it closes, Android can
 * leave the WebView without input focus, so the keyboard and pickers stop opening.
 * Give focus back to the WebView every time this screen becomes active again.
 */
public class MainActivity extends BridgeActivity {
    private void focusWebView() {
        try {
            if (getBridge() == null) return;
            WebView wv = getBridge().getWebView();
            if (wv == null) return;
            wv.setFocusable(true);
            wv.setFocusableInTouchMode(true);
            if (!wv.hasFocus()) wv.requestFocus(View.FOCUS_DOWN);
        } catch (Exception ignored) { }
    }

    @Override
    public void onResume() {
        super.onResume();
        focusWebView();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) focusWebView();
    }
}
