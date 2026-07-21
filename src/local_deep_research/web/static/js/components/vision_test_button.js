(function () {
    "use strict";

    /**
     * Attach a "Test Connection" button next to the vision URL field.
     * On click, reads the three vision fields and POSTs to /api/vision/test_connection.
     * Shows a toast with the result.
     */
    function attachTestButton(urlInput) {
        if (!urlInput) return;
        const row = urlInput.closest(".ldr-settings-row, .form-row, div");
        if (!row) return;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ldr-btn ldr-btn-secondary vision-test-btn";
        btn.textContent = i18n.t("Test Connection");
        btn.style.marginLeft = "0.5rem";
        btn.addEventListener("click", async function () {
            const apiKeyInput = document.querySelector(
                "input[name='report.image_vision_api_key']"
            );
            const modelInput = document.querySelector(
                "select[name='report.image_vision_model'], input[name='report.image_vision_model']"
            );
            const url = urlInput.value;
            const api_key = apiKeyInput ? apiKeyInput.value : "";
            const model = modelInput ? modelInput.value : "";
            btn.disabled = true;
            const originalText = btn.textContent;
            btn.textContent = i18n.t("Testing...");
            try {
                const resp = await fetch("/api/vision/test_connection", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url, api_key, model }),
                });
                const data = await resp.json();
                if (data.success) {
                    showAlert(
                        i18n.t("Vision connected (%sms)", data.latency_ms),
                        "success"
                    );
                } else {
                    showAlert(
                        i18n.t("Vision connection failed: %s", data.error),
                        "error"
                    );
                }
            } catch (e) {
                showAlert(
                    i18n.t("Vision connection failed: %s", String(e)),
                    "error"
                );
            } finally {
                btn.disabled = false;
                btn.textContent = originalText;
            }
        });
        // Insert after the URL input
        if (urlInput.parentElement) {
            urlInput.parentElement.appendChild(btn);
        } else {
            row.appendChild(btn);
        }
    }

    function showAlert(message, type) {
        // Reuse existing alert system if available; otherwise show a simple alert.
        if (typeof window.showAlert === "function") {
            window.showAlert(message, type);
        } else {
            alert(message);
        }
    }

    // Expose
    window.attachVisionTestButton = attachTestButton;
})();
