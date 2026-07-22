(function () {
    "use strict";

    /**
     * Attach a "Test Connection" button next to the vision URL field.
     * On click, reads the three vision fields, POSTs to
     * /api/vision/test_connection with the LDR CSRF token, and
     * shows a toast with the result.
     *
     * IMPORTANT: LDR's CSRFProtect requires either a "csrf_token"
     * form field or an "X-CSRFToken" header on every POST. We use
     * window.api.getCsrfToken() (the same helper other components
     * like history.js / results.js / settings_sync.js use) so the
     * test button works in the WebUI out of the box.
     */
    function attachTestButton(urlInput) {
        if (!urlInput) return;
        // Idempotency: remove any previously injected button in this
        // setting item (renderSettingsByTab is called on every tab
        // switch, so without this we'd accumulate one button per render).
        const parent = urlInput.parentElement;
        if (parent) {
            const existing = parent.querySelector(".vision-test-btn");
            if (existing) existing.remove();
        }

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ldr-btn ldr-btn-secondary vision-test-btn";
        btn.textContent = i18n.t("Test Connection");
        btn.style.marginLeft = "0.5rem";

        btn.addEventListener("click", async function () {
            const apiKeyInput = document.querySelector(
                "input[name='report.image_vision_api_key']"
            );
            // Vision model may render as a <select> (allowCustom select
            // with hidden input) or as a plain <input> depending on the
            // settings UI implementation.
            const modelSelect = document.querySelector(
                "select[name='report.image_vision_model']"
            );
            const modelHidden = document.querySelector(
                "input[name='report.image_vision_model']"
            );
            const model =
                (modelSelect && modelSelect.value) ||
                (modelHidden && modelHidden.value) ||
                "";
            const url = urlInput.value || "";
            const apiKey = apiKeyInput ? apiKeyInput.value || "" : "";

            btn.disabled = true;
            const originalText = btn.textContent;
            btn.textContent = i18n.t("Testing...");

            // Resolve CSRF token via the same helper other components
            // use (settings_sync.js, history.js, results.js). Fallback
            // to a meta tag if window.api isn't ready yet.
            let csrfToken = "";
            if (window.api && typeof window.api.getCsrfToken === "function") {
                try {
                    csrfToken = window.api.getCsrfToken() || "";
                } catch (e) {
                    csrfToken = "";
                }
            }
            if (!csrfToken) {
                const meta = document.querySelector(
                    'meta[name="csrf-token"]'
                );
                if (meta) csrfToken = meta.content || "";
            }

            try {
                const headers = { "Content-Type": "application/json" };
                if (csrfToken) headers["X-CSRFToken"] = csrfToken;

                const resp = await fetch(
                    "/api/vision/test_connection",
                    {
                        method: "POST",
                        headers: headers,
                        credentials: "same-origin",
                        body: JSON.stringify({
                            url: url,
                            api_key: apiKey,
                            model: model,
                        }),
                    }
                );

                // Try to parse JSON; if the server returned a non-JSON
                // HTML error page, surface the raw text instead of
                // letting the catch block turn it into "TypeError".
                let data;
                try {
                    data = await resp.json();
                } catch (parseErr) {
                    const text = await resp.text();
                    showAlert(
                        i18n.t(
                            "Vision connection failed: HTTP %s — %s",
                            String(resp.status),
                            text.slice(0, 200)
                        ),
                        "error",
                        false
                    );
                    return;
                }

                if (data.success) {
                    showAlert(
                        i18n.t(
                            "Vision connected (%sms)",
                            data.latency_ms != null
                                ? String(data.latency_ms)
                                : "?"
                        ),
                        "success",
                        false
                    );
                } else {
                    showAlert(
                        i18n.t(
                            "Vision connection failed: %s",
                            data.error || "unknown error"
                        ),
                        "error",
                        false
                    );
                }
            } catch (e) {
                // Network failure / CORS / etc.
                showAlert(
                    i18n.t(
                        "Vision connection failed: %s",
                        String(e && e.message ? e.message : e)
                    ),
                    "error",
                    false
                );
            } finally {
                btn.disabled = false;
                btn.textContent = originalText;
            }
        });

        // Insert the button right after the URL input in its parent.
        if (parent) {
            parent.appendChild(btn);
        } else {
            urlInput.insertAdjacentElement("afterend", btn);
        }
    }

    /**
     * Show a toast using the WebUI's actual exported UI service
     * (window.ui.showAlert from src/local_deep_research/web/static/js/
     * services/ui.js). settings.js keeps its own showAlert private
     * inside an IIFE, so window.showAlert is undefined; routing
     * through window.ui.showAlert is what makes the toast actually
     * visible to the user.
     *
     * Fallback chain (each level is independently guarded):
     *   1. window.ui.showAlert           — the LDR UI service
     *   2. window.api.showAlert           — older alias used by some
     *                                       legacy components
     *   3. window.showAlert               — third-party / older scripts
     *   4. console.{log,error}           — last-resort, lets the user
     *                                       inspect via DevTools
     */
    function showAlert(message, type, skipIfToastShown) {
        const variants = [
            window.ui && window.ui.showAlert,
            window.api && window.api.showAlert,
            window.showAlert,
        ];
        for (const fn of variants) {
            if (typeof fn === "function") {
                try {
                    // Pass skipIfToastShown through so the caller can
                    // force the toast to appear even when the LDR UI
                    // service thinks another toast is already on screen.
                    // vision_test_connection results are always worth
                    // surfacing; we pass `false` from every call site.
                    fn(message, type, skipIfToastShown);
                    return;
                } catch (_) {
                    /* try next variant */
                }
            }
        }
        try {
            if (type === "error") {
                console.error("[vision-test]", message);
            } else {
                console.log("[vision-test]", message);
            }
        } catch (_) {
            /* no console either; genuinely nothing we can do */
        }
    }

    // Expose
    window.attachVisionTestButton = attachTestButton;
})();