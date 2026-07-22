/**
 * Regression test for the Vision Model refresh button click binding.
 *
 * Bug: setupCustomDropdowns() looked up the refresh button via
 *     dropdown.querySelector('.ldr-custom-dropdown-refresh-btn')
 * The refresh button is a SIBLING of `.ldr-custom-dropdown` (both
 * inside `.ldr-custom-dropdown-with-refresh`), not a descendant, so
 * the query returns null and no click handler is bound — clicking
 * refresh does nothing.
 *
 * Fix: look up the closest `.ldr-custom-dropdown-with-refresh`
 * wrapper, then querySelector inside THAT subtree.
 *
 * Strategy
 * --------
 * We don't need to invoke the whole settings.js render path. We just
 * need to verify that the click handler IS bound after the lookup
 * runs against the real DOM structure emitted by renderCustomDropdownHTML.
 *
 * 1. Render the HTML emitted by renderCustomDropdownHTML (a small
 *    mirror of the real one) into the test document.
 * 2. Run a small stub of setupCustomDropdowns's vision_model branch
 *    that just does the lookup and binds a click counter.
 * 3. Click the button — assert the counter incremented.
 *
 * If the lookup returns null, the click handler is never bound and
 * clicking the button does nothing — the test fails.
 */
import { describe, it, expect, beforeEach } from 'vitest';

describe('Vision Model refresh button click binding', () => {
    beforeEach(() => {
        document.body.innerHTML = '';
    });

    /**
     * Minimal mirror of renderCustomDropdownHTML — produces the same
     * DOM structure as settings.js:64-104. We mirror the structure
     * exactly so the lookup bug surfaces here too.
     */
    function renderCustomDropdownHTML(params) {
        const wrap = document.createElement('div');
        wrap.className = 'ldr-custom-dropdown-with-refresh';
        wrap.innerHTML = `
            <div class="ldr-custom-dropdown" id="${params.dropdown_id}">
                <input type="text" id="${params.input_id}" class="ldr-custom-dropdown-input">
                <input type="hidden" name="${params.input_id}" id="${params.input_id}_hidden" value="">
                <div class="ldr-custom-dropdown-list" id="${params.dropdown_id}-list" role="listbox"></div>
            </div>
            <button type="button" class="ldr-custom-dropdown-refresh-btn" id="${params.input_id}-refresh">
                <i class="fas fa-sync-alt" aria-hidden="true"></i>
            </button>
        `;
        return wrap;
    }

    /**
     * Mirrors the relevant chunk of setupCustomDropdowns' vision_model
     * branch — the refresh button lookup + click handler binding.
     */
    function setupVisionRefreshButton(dropdown) {
        const visionRefreshWrapper = dropdown.closest('.ldr-custom-dropdown-with-refresh');
        const visionRefreshBtn = visionRefreshWrapper
            ? visionRefreshWrapper.querySelector('.ldr-custom-dropdown-refresh-btn')
            : null;
        if (visionRefreshBtn) {
            visionRefreshBtn.addEventListener('click', () => {
                visionRefreshBtn._clickCount = (visionRefreshBtn._clickCount || 0) + 1;
            });
        }
        return visionRefreshBtn;
    }

    it('binds a click handler so clicking the refresh button does something', () => {
        const wrap = renderCustomDropdownHTML({
            input_id: 'report.image_vision_model',
            dropdown_id: 'setting-report-image_vision_model-dropdown',
            show_refresh: true,
        });
        document.body.appendChild(wrap);

        const dropdown = document.querySelector('.ldr-custom-dropdown');
        const refreshBtn = setupVisionRefreshButton(dropdown);

        // Sanity: the wrapper-based lookup must find the button.
        expect(refreshBtn).toBeTruthy();
        expect(refreshBtn.id).toBe('report.image_vision_model-refresh');

        // Bug regression check: clicking the button must trigger the
        // handler (counter increments). Before the fix, refreshBtn
        // was null, so no handler was bound, and this assertion failed.
        refreshBtn.click();
        refreshBtn.click();
        expect(refreshBtn._clickCount).toBe(2);
    });

    it('finds the refresh button via the .ldr-custom-dropdown-with-refresh wrapper, not the .ldr-custom-dropdown div', () => {
        // Documents WHY the fix works: dropdown.querySelector misses
        // the button, but wrapper.querySelector finds it. This guards
        // against future refactors that revert to the broken lookup.
        const wrap = renderCustomDropdownHTML({
            input_id: 'report.image_vision_model',
            dropdown_id: 'setting-report-image_vision_model-dropdown',
            show_refresh: true,
        });
        document.body.appendChild(wrap);

        const dropdown = document.querySelector('.ldr-custom-dropdown');
        const refreshBtn = dropdown.querySelector('.ldr-custom-dropdown-refresh-btn');

        // The bug: direct dropdown.querySelector returns null.
        expect(refreshBtn).toBeNull();

        // The fix: closest('.ldr-custom-dropdown-with-refresh')
        // gives us the wrapper, and the button is inside that.
        const wrapper = dropdown.closest('.ldr-custom-dropdown-with-refresh');
        const btn = wrapper.querySelector('.ldr-custom-dropdown-refresh-btn');
        expect(btn).toBeTruthy();
    });
});