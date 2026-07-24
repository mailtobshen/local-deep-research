/**
 * Lightweight i18n service for the frontend.
 * Mirrors the backend Translator class by loading JSON dictionaries.
 */
(function() {
    'use strict';

    const DEFAULT_LANGUAGE = 'zh';
    const SUPPORTED_LANGUAGES = ['zh', 'en'];
    const STORAGE_KEY_PREFIX = 'ldr-language';

    function getUserId() {
        const meta = document.querySelector('meta[name="user-id"]');
        return meta ? meta.getAttribute('content') : 'anonymous';
    }

    function getStorageKey() {
        return STORAGE_KEY_PREFIX + '-' + getUserId();
    }

    // Current language detection order:
    // 1. HTML lang attribute (set by server)
    // 2. localStorage
    // 3. Default zh
       let currentLanguage = document.documentElement.lang ||
        localStorage.getItem(getStorageKey()) ||
        DEFAULT_LANGUAGE;

    if (SUPPORTED_LANGUAGES.indexOf(currentLanguage) === -1) {
        currentLanguage = DEFAULT_LANGUAGE;
    }

    // Translation dictionary
    let translations = {};
    let isLoaded = false;
    let loadPromise = null;

    // Use server-inlined translations if available (avoids async fetch delay)
    if (typeof window !== 'undefined' && window.__TRANSLATIONS__) {
        translations = window.__TRANSLATIONS__;
        isLoaded = true;
    }
    if (typeof window !== 'undefined' && window.__CURRENT_LANGUAGE__) {
        currentLanguage = window.__CURRENT_LANGUAGE__;
        document.documentElement.lang = currentLanguage;
    }

    function loadTranslations(lang) {
        if (loadPromise && currentLanguage === lang && isLoaded) {
            return Promise.resolve(translations);
        }

        currentLanguage = lang;
        // If server provided the requested language, use it directly
        if (window.__TRANSLATIONS__ && window.__CURRENT_LANGUAGE__ === lang) {
            translations = window.__TRANSLATIONS__;
            isLoaded = true;
            document.documentElement.lang = lang;
            localStorage.setItem(getStorageKey(), lang);
            loadPromise = Promise.resolve(translations);
            return loadPromise;
        }

        loadPromise = fetch('/static/translations/' + lang + '.json')
            .then(function(resp) {
                if (!resp.ok) {
                    throw new Error('Failed to load translations for ' + lang);
                }
                return resp.json();
            })
            .then(function(dict) {
                translations = dict;
                isLoaded = true;
                document.documentElement.lang = lang;
                localStorage.setItem(getStorageKey(), lang);
                return translations;
            })
            .catch(function(err) {
                console.warn('i18n load error:', err);
                translations = {};
                isLoaded = true;
                return translations;
            });

        return loadPromise;
    }

    /**
     * Translate a string.
     * @param {string} key - The English source text.
     * @param {Object} [vars] - Optional interpolation variables {name: value}.
     * @returns {string} The translated text, or the original key if not found.
     */
    function t(key, vars) {
        if (!key) {
            return '';
        }
        var text = translations[key] || key;
        if (vars) {
            Object.keys(vars).forEach(function(k) {
                text = text.replace(new RegExp('\\{\\{\\s*' + k + '\\s*\\}\\}', 'g'), vars[k]);
            });
        }
        return text;
    }

    /**
     * Translate a string and format it with arguments (printf-style).
     * @param {string} key - The English source text.
     * @param {...*} args - Values to replace %s, %d, etc.
     * @returns {string} The formatted translated text.
     */
    function tf(key, ...args) {
        let text = t(key);
        let idx = 0;
        text = text.replace(/%([sdj%])/g, function(match, fmt) {
            if (fmt === '%') {
                return '%';
            }
            if (idx < args.length) {
                const val = args[idx++];
                if (fmt === 'd') {
                    return parseInt(val, 10);
                }
                if (fmt === 'j') {
                    return JSON.stringify(val);
                }
                return String(val);
            }
            return match;
        });
        return text;
    }

    /**
     * Save language preference to the server.
     * @param {string} lang
     * @returns {Promise}
     */
    function saveToServer(lang) {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
        return fetch('/settings/api/app.language', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken || ''
            },
            body: JSON.stringify({ value: lang })
        }).catch(function(err) {
            console.warn('Failed to save language to server:', err);
        });
    }

    // Initialize: load translations for the current language
    loadTranslations(currentLanguage);

    // Translate an API error response body into a user-facing message.
    // Server may return one of three shapes:
    //   - { message_key, message_args, hint_key }  → i18n key + interpolation
    //   - { message: "<English text>" }             → translate literal string
    //   - { message_raw: "<text>" }                → use raw (parse-failed fallback)
    function renderApiErrorMessage(errorData) {
        if (!errorData) return t('Failed to start research');
        if (errorData.message_key) {
            const msg = tf(errorData.message_key, errorData.message_args || {});
            const hint = errorData.hint_key ? t(errorData.hint_key) : '';
            return hint ? `${msg} — ${hint}` : msg;
        }
        if (errorData.message_raw) return errorData.message_raw;
        return errorData.message || t('Failed to start research');
    }

    // Expose global API
    window.i18n = {
        t: t,
        tf: tf,
        loadTranslations: loadTranslations,
        saveToServer: saveToServer,
        renderApiErrorMessage: renderApiErrorMessage,
        get currentLanguage() {
            return currentLanguage;
        },
        get isLoaded() {
            return isLoaded;
        }
    };
})();
