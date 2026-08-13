(function () {
    "use strict";

    /**
     * Inline status display for the Vision Endpoint Test Connection
     * button.
     *
     * Design decisions (deliberately conservative):
     * - No toast service dependency. Previous revisions called
     *   window.ui.showAlert, which on the settings page lands inside
     *   an off-screen container (~53,000 px below the fold) and
     *   disappears before the user can scroll to it. Using an inline
     *   status node placed right after the button keeps feedback
     *   inside the user's viewport by construction.
     * - No i18n.t (positional args silently dropped). Use
     *   document.createTextNode with literal message + status
     *   indicator. Concise English is fine for a developer-facing
     *   test surface.
     * - Idempotent attachment. renderSettingsByTab runs on every tab
     *   switch; the same button must not stack up.
     */

    /**
     * Build (or reuse) an inline status <span> placed right after the
     * Test Connection button. Updates its text + color in place.
     */
    function getStatusEl(btn) {
        // The status lives in the same flex row as the button; if it's
        // missing we create it. Reuse an existing one if present so
        // repeated clicks don't multiply status nodes.
        let status = btn.parentElement.querySelector(
            ".vision-test-status"
        );
        if (!status) {
            status = document.createElement("span");
            status.className = "vision-test-status";
            status.style.marginLeft = "0.5rem";
            status.style.fontSize = "0.9em";
            status.style.fontWeight = "500";
            // Insert right after the button.
            btn.insertAdjacentElement("afterend", status);
        }
        return status;
    }

    function setStatus(statusEl, kind, text) {
        // kind in {"idle","pending","ok","err"} — color mapping.
        // Use inline styles so we don't depend on a CSS file.
        statusEl.textContent = "";
        statusEl.appendChild(document.createTextNode(text));
        statusEl.dataset.kind = kind;
        if (kind === "pending") {
            statusEl.style.color = "#666";
        } else if (kind === "ok") {
            statusEl.style.color = "#1a7f37";
        } else if (kind === "err") {
            statusEl.style.color = "#b91c1c";
        } else {
            statusEl.style.color = "#666";
        }
    }

    function attachTestButton(urlInput) {
        if (!urlInput) return;
        const parent = urlInput.parentElement;
        if (!parent) return;

        // Idempotency: remove any previously injected button, status,
        // AND wrapper from this setting item. renderSettingsByTab fires
        // on every tab switch and would otherwise stack them.
        parent
            .querySelectorAll(".vision-test-btn, .vision-test-status, .vision-url-row")
            .forEach((el) => el.remove());

        // Layout: put the URL input + Test Connection button on one row.
        // Wrap them in a flex container so the (now half-width) input
        // sits on the left and the button on its right with a gap. The
        // wrapper is inserted where the input currently lives, and the
        // input is moved into it — preserving its DOM position relative
        // to the surrounding label/help-text rendered by settings.js.
        const row = document.createElement("div");
        row.className = "vision-url-row";
        row.style.display = "flex";
        row.style.alignItems = "center";
        row.style.gap = "0.75rem";
        row.style.width = "100%";
        parent.insertBefore(row, urlInput);
        row.appendChild(urlInput);

        // Halve the input width so the button fits beside it on the row.
        urlInput.style.width = "calc(50% - 0.375rem)";
        urlInput.style.flex = "0 0 auto";

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-secondary btn-sm vision-test-btn";
        btn.textContent = "连接测试";
        btn.style.flexShrink = "0";
        row.appendChild(btn);

        const status = getStatusEl(btn);

        btn.addEventListener("click", async function () {
            // Read the four vision fields:
            //   - report.image_vision_provider (new — drives chat dispatch)
            //   - report.image_vision_model    (filtered by provider)
            //   - report.image_vision_url      (auto-prefilled per provider)
            //   - report.image_vision_api_key
            const providerSelect = document.querySelector(
                "select[name='report.image_vision_provider']"
            );
            const apiKeyInput = document.querySelector(
                "input[name='report.image_vision_api_key']"
            );
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
            const provider = providerSelect ? providerSelect.value || "openai_endpoint" : "openai_endpoint";
            const url = urlInput.value || "";
            const apiKey = apiKeyInput ? apiKeyInput.value || "" : "";

            // The model dropdown is filtered by provider, so the
            // selected value is always a real model name. We do NOT
            // need a custom-prompt flow here (the previous
            // '__custom__' sentinel was removed when the
            // provider-tagged option list shipped).

            btn.disabled = true;
            const originalText = btn.textContent;
            btn.textContent = "测试中…";
            setStatus(status, "pending", " 测试中…");

            // CSRF token — same pattern the rest of LDR uses.
            let csrfToken = "";
            if (
                window.api &&
                typeof window.api.getCsrfToken === "function"
            ) {
                try {
                    csrfToken = window.api.getCsrfToken() || "";
                } catch (_) {
                    /* fall through */
                }
            }
            if (!csrfToken) {
                const meta = document.querySelector(
                    'meta[name="csrf-token"]'
                );
                if (meta) csrfToken = meta.content || "";
            }

            function finish(kind, text) {
                btn.disabled = false;
                btn.textContent = originalText;
                setStatus(status, kind, text);
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
                            provider: provider,
                            url: url,
                            api_key: apiKey,
                            model: model,
                        }),
                    }
                );

                // Read the body as text first so a non-JSON HTML
                // error page (e.g. proxy 502) surfaces something
                // useful instead of becoming "SyntaxError".
                let data;
                const text = await resp.text();
                try {
                    data = JSON.parse(text);
                } catch (_) {
                    finish(
                        "err",
                        ` HTTP ${resp.status} — ${text.slice(0, 160)}`
                    );
                    return;
                }

                if (data.success) {
                    const ms =
                        data.latency_ms != null
                            ? String(data.latency_ms)
                            : "?";
                    finish("ok", ` ✓ 连接成功 (${ms} 毫秒)`);
                } else {
                    finish(
                        "err",
                        ` ✗ ${(data.error || "未知错误").slice(0, 200)}`
                    );
                }
            } catch (e) {
                finish(
                    "err",
                    ` ✗ ${String((e && e.message) || e).slice(0, 200)}`
                );
            }
        });
    }

    // Expose
    window.attachVisionTestButton = attachTestButton;
})();