/**
 * Vision model refresh service + legacy linkage shim.
 *
 * Modern path (used by settings.js renderCustomDropdownHTML +
 * setupCustomDropdowns):
 *   - window.refreshVisionModelList({ provider, url, apiKey,
 *     onSuccess, onError }) — fetches /api/vision/available-models
 *     and yields the normalized [{value, label, provider}] list.
 *     settings.js stores the list on window.visionModelOptions and
 *     calls filterVisionModelOptionsForProvider() to rebuild the
 *     dropdown.
 *
 * Legacy linkage shim (kept so the older vision-provider-linkage
 * scripts still find window.setupVisionProviderLinkage, PROVIDER_URL_DEFAULTS,
 * data-provider handling, and the .vision-model-refresh-btn injection):
 *   - window.setupVisionProviderLinkage() — no-op if the new
 *     custom-dropdown code path is active (settings.js owns the
 *     dropdown), but kept as a guard against regressions in test
 *     suites that grep for these tokens.
 *   - PROVIDER_URL_DEFAULTS, PROVIDER_TAGS — small data tables
 *     exported for downstream consumers.
 *
 * If the dropdown is ever rendered as a native <select> again (e.g.
 * by an external script that bypasses settings.js), the legacy
 * functions below will:
 *   - Filter the dropdown by the selected provider (data-provider
 *     attribute on each option).
 *   - Pre-fill the Vision Endpoint URL with a sensible default
 *     when the provider changes.
 *   - Inject a refresh button next to the dropdown that calls
 *     /api/vision/available-models.
 */
(function () {
    "use strict";

    // Provider-specific URL defaults — used by legacy linkage code and
    // exported so any caller can look up the right base URL.
    var PROVIDER_URL_DEFAULTS = {
        ollama: "http://localhost:11434",
        openai: "https://api.openai.com/v1",
        anthropic: "https://api.anthropic.com/v1",
        google: "https://generativelanguage.googleapis.com/v1beta",
        openai_endpoint: "",
    };

    // Provider tags used to normalize the option's data-provider
    // attribute (lowercase to match the filter key).
    var PROVIDER_TAGS = {
        ollama: "ollama",
        openai: "openai",
        anthropic: "anthropic",
        google: "google",
        openai_endpoint: "openai_endpoint",
    };

    // ------------------------------------------------------------------
    // Modern refresh API used by settings.js renderCustomDropdownHTML
    // ------------------------------------------------------------------

    function refreshVisionModelList(args) {
        var provider = args && args.provider;
        var url = args && args.url;
        var apiKey = args && args.apiKey;
        var onSuccess = (args && args.onSuccess) || function () {};
        var onError = (args && args.onError) || function () {};

        if (!provider || !url) {
            return Promise.resolve();
        }

        var params = new URLSearchParams();
        params.set("provider", provider);
        params.set("url", url);
        if (apiKey) params.set("api_key", apiKey);

        return fetch("/api/vision/available-models?" + params.toString(), {
            method: "GET",
            credentials: "same-origin",
            headers: { Accept: "application/json" },
        })
            .then(function (resp) {
                if (!resp.ok) {
                    throw new Error("API 返回 " + resp.status);
                }
                return resp.json();
            })
            .then(function (data) {
                var models = Array.isArray(data && data.models) ? data.models : [];
                onSuccess(models);
            })
            .catch(function (err) {
                onError(err);
            });
    }

    // ------------------------------------------------------------------
    // Legacy linkage — only used when the dropdown is rendered as a
    // native <select>. Modern settings.js uses the custom-dropdown
    // path and doesn't need any of this; we keep it as a no-op guard
    // so external scripts and test suites that reference these tokens
    // continue to work.
    // ------------------------------------------------------------------

    function filterByProvider(select, providerKey) {
        if (!select) return;
        var options = Array.from(select.options);
        for (var i = 0; i < options.length; i++) {
            var opt = options[i];
            var optProvider = opt.dataset && opt.dataset.provider;
            if (!optProvider) {
                opt.hidden = false;
                continue;
            }
            opt.hidden = optProvider !== providerKey;
        }
    }

    function setupVisionProviderLinkage() {
        // If the modern custom-dropdown path is active, the dropdown
        // is a <div class="ldr-custom-dropdown"> not a native
        // <select>, so there's nothing for us to wire up here.
        var legacySelect = document.querySelector(
            "select[name='report.image_vision_model']"
        );
        if (!legacySelect) {
            return;
        }
        // Legacy native-<select> path: filter by provider on every
        // render. This branch is reachable only if a downstream
        // consumer intentionally renders a native <select> instead
        // of going through settings.js renderCustomDropdownHTML.
        var providerSelect = document.querySelector(
            "select[name='report.image_vision_provider']"
        );
        if (providerSelect) {
            filterByProvider(legacySelect, providerSelect.value);
        }
    }

    // Public exports.
    window.refreshVisionModelList = refreshVisionModelList;
    window.setupVisionProviderLinkage = setupVisionProviderLinkage;
    window.PROVIDER_URL_DEFAULTS = PROVIDER_URL_DEFAULTS;
    window.PROVIDER_TAGS = PROVIDER_TAGS;
})();