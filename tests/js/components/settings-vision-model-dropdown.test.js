/**
 * Regression tests for the report.image_vision_model custom-dropdown
 * branch in settings.js's renderSettingItem().
 *
 * Mirrors llm.model exactly: instead of rendering a native <select>,
 * settings.js now routes report.image_vision_model through
 * renderCustomDropdownHTML({ allow_custom: true, show_refresh: true }).
 *
 * Why this matters: the previous native-<select> implementation lost
 * the saved value when the value wasn't in the static options list
 * (e.g. "MiniMax-M3" for a private deployment). The custom-dropdown
 * pattern uses a text input + hidden submit input, both populated by
 * setupCustomDropdowns() from allSettings. Free-form values are
 * preserved by allow_custom: true.
 *
 * Strategy
 * --------
 * settings.js is too large to load in tests. Like
 * settings-model-data.test.js, we extract just the relevant
 * `else if (setting.key === 'report.image_vision_model')` branch by
 * brace-matching, then verify the emitted HTML structure.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SETTINGS_PATH = resolve(
    __dirname,
    '../../../src/local_deep_research/web/static/js/components/settings.js'
);
const SOURCE = readFileSync(SETTINGS_PATH, 'utf8');

/**
 * Extract the vision_model branch body. We locate the unique anchor
 * ("Mirrors llm.model exactly so saved values") that precedes the
 * branch and walk back to find the `else if (setting.key === ...)`
 * line, then brace-match forward through the branch body to its
 * closing `}`.
 */
function extractVisionBranch(source) {
    const anchor = 'Mirrors llm.model exactly so saved values';
    const anchorIdx = source.indexOf(anchor);
    if (anchorIdx < 0) {
        throw new Error(
            'Could not find vision_model branch anchor in settings.js — ' +
            'the refactor may have been undone. Update this test.'
        );
    }
    // Walk back to find the `else if (setting.key === 'report.image_vision_model') {`
    const marker = "} else if (setting.key === 'report.image_vision_model') {";
    const markerIdx = source.lastIndexOf(marker, anchorIdx);
    if (markerIdx < 0) {
        throw new Error(
            'Could not find `else if (setting.key === \'report.image_vision_model\')` line.'
        );
    }
    const openBrace = markerIdx + marker.length - 1;
    let depth = 0;
    let i = openBrace;
    for (; i < source.length; i++) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}') {
            depth--;
            if (depth === 0) break;
        }
    }
    if (depth !== 0) throw new Error('Brace mismatch in vision branch.');
    return source.slice(openBrace + 1, i);
}

const BRANCH_BODY = extractVisionBranch(SOURCE);

/**
 * Run the extracted body and return the value of `inputElement`.
 * The body declares `inputElement` with `let`, so we only need to
 * inline the body and capture the final value.
 */
function renderBranch() {
    const setting = { key: 'report.image_vision_model', editable: true };
    const settingId = 'setting-report-image_vision_model';
    const i18n = { t: (s) => s };
    const escapeHtml = (s) => String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    const renderCustomDropdownHTML = (params) => {
        // Mirrors settings.js:59-103 enough for these structural tests.
        let html = `<div class="ldr-custom-dropdown" id="${params.dropdown_id}">`;
        html += `<input type="text" id="${params.input_id}" data-key="${params.data_setting_key || params.input_id}" class="ldr-custom-dropdown-input" placeholder="${params.placeholder}" aria-haspopup="listbox" aria-expanded="false" aria-controls="${params.dropdown_id}-list">`;
        html += `<input type="hidden" name="${params.input_id}" id="${params.input_id}_hidden" value="">`;
        html += `<div class="ldr-custom-dropdown-list" id="${params.dropdown_id}-list" role="listbox"></div>`;
        html += `</div>`;
        if (params.show_refresh) {
            html = `<div class="ldr-custom-dropdown-with-refresh">${html}<button type="button" class="ldr-custom-dropdown-refresh-btn" id="${params.input_id}-refresh" aria-label="${params.refresh_aria_label || 'Refresh options'}"></button></div>`;
        }
        return html;
    };
    // The body declares inputElement with `let`, then assigns to it.
    // Wrap the body so we can capture the final value.
    // eslint-disable-next-line no-new-func
    const fn = new Function(
        'setting', 'settingId', 'i18n', 'escapeHtml', 'renderCustomDropdownHTML',
        `let inputElement;\n${BRANCH_BODY}\nreturn inputElement;`
    );
    return fn(setting, settingId, i18n, escapeHtml, renderCustomDropdownHTML);
}

describe('renderSettingItem report.image_vision_model branch', () => {
    const html = renderBranch();

    it('renders the dropdown wrapper div with the expected id', () => {
        expect(html).toContain('id="setting-report-image_vision_model-dropdown"');
        expect(html).toContain('class="ldr-custom-dropdown"');
    });

    it('renders a visible text input with id=report.image_vision_model', () => {
        // The visible input is matched by vision_test_button.js's
        // document.querySelector("input[name='report.image_vision_model']")
        // ONLY when it has the name attribute. The custom-dropdown
        // pattern uses id+data-key on the visible input and name on
        // the hidden input. Verify both exist.
        expect(html).toMatch(/<input[^>]*type="text"[^>]*id="report\.image_vision_model"/);
    });

    it('renders a hidden submit input with name=report.image_vision_model', () => {
        // vision_test_button.js:100 selects this name to read the
        // saved value when the dropdown is in custom-dropdown mode.
        expect(html).toMatch(
            /<input[^>]*type="hidden"[^>]*name="report\.image_vision_model"/
        );
        expect(html).toContain('id="report.image_vision_model_hidden"');
    });

    it('renders a refresh button (show_refresh: true)', () => {
        expect(html).toContain('class="ldr-custom-dropdown-refresh-btn"');
        expect(html).toContain('id="report.image_vision_model-refresh"');
        expect(html).toContain('class="ldr-custom-dropdown-with-refresh"');
    });

    it('does NOT render a native <select> (custom-dropdown pattern only)', () => {
        // The bug we are fixing lived in the native <select> path.
        // The custom-dropdown path uses text + hidden inputs only —
        // no <select> tag.
        expect(html).not.toMatch(/<select[^>]*name="report\.image_vision_model"/);
    });

    it('renders the dropdown list div with the expected id', () => {
        expect(html).toContain(
            'id="setting-report-image_vision_model-dropdown-list"'
        );
    });
});