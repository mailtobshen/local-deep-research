/**
 * Language Switcher Component
 * Handles language selection, persistence, and page reload.
 *
 * Because Jinja2 templates are server-rendered, a language change
 * triggers a full page reload so the server can re-render in the
 * new locale.
 */
(function() {
    'use strict';

    const STORAGE_KEY_PREFIX = 'ldr-language';
    const SETTING_KEY = 'app.language';
    const SUPPORTED_LANGUAGES = ['zh', 'en'];

    function getUserId() {
        const meta = document.querySelector('meta[name="user-id"]');
        return meta ? meta.getAttribute('content') : 'anonymous';
    }

    function getStorageKey() {
        return STORAGE_KEY_PREFIX + '-' + getUserId();
    }

    function getCurrentLanguage() {
        return localStorage.getItem(getStorageKey()) || 'zh';
    }

    function setCookie(name, value, days) {
        var expires = '';
        if (days) {
            var date = new Date();
            date.setTime(date.getTime() + days * 24 * 60 * 60 * 1000);
            expires = '; expires=' + date.toUTCString();
        }
        document.cookie = name + '=' + encodeURIComponent(value) + expires + '; path=/; SameSite=Lax';
    }

    function setLanguage(lang, options) {
        options = options || {};
        var reload = options.reload !== false;

        if (SUPPORTED_LANGUAGES.indexOf(lang) === -1) {
            console.warn('Unsupported language:', lang);
            return Promise.resolve();
        }

        localStorage.setItem(getStorageKey(), lang);
        // Also set a cookie so the backend remembers the language across page navigations
        setCookie('locale', lang, 365);

        var savePromise;
        if (window.i18n && window.i18n.saveToServer) {
            savePromise = window.i18n.saveToServer(lang);
        } else {
            var csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
            savePromise = fetch('/settings/api/' + SETTING_KEY, {
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

        if (reload) {
            savePromise.finally(function() {
                var url = new URL(window.location.href);
                url.searchParams.set('lang', lang);
                window.location.href = url.toString();
            });
        }

        return savePromise;
    }

    function setupHeaderDropdown() {
        var dropdown = document.getElementById('language-dropdown');
        if (!dropdown) return;

        var currentLang = getCurrentLanguage();
        if (SUPPORTED_LANGUAGES.indexOf(dropdown.value) === -1) {
            dropdown.value = currentLang;
        }

        if (!dropdown.dataset.languageInitialized) {
            dropdown.dataset.languageInitialized = 'true';

            dropdown.addEventListener('change', function(e) {
                setLanguage(e.target.value);
            });
        }
    }

    function initialize() {
        setupHeaderDropdown();

        window.addEventListener('pageshow', function(event) {
            if (event.persisted) {
                setupHeaderDropdown();
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize);
    } else {
        initialize();
    }

    window.languageSwitcher = {
        setLanguage: setLanguage,
        getCurrentLanguage: getCurrentLanguage,
        SUPPORTED_LANGUAGES: SUPPORTED_LANGUAGES
    };
})();
