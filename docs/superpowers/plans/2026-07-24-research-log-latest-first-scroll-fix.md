# Research Log Latest-First Scroll Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the research log’s newest-first visual order while ensuring automatic scrolling remains at the visual top when new records arrive.

**Architecture:** Preserve the existing `flex-direction: column-reverse` layout and chronological DOM ordering. Centralize the visual-top scroll operation in a small helper inside `logpanel.js`, call it after live insertion and successful batch rendering, and cover both paths with Vitest regression tests. Do not change filtering, deduplication, ordering, or retention behavior.

**Tech Stack:** Vanilla JavaScript, DOM APIs, Vitest, jsdom, existing log-panel test harness.

## Global Constraints

- Keep newest records visually at the top.
- Do not alter the existing `column-reverse` CSS layout or chronological DOM ordering.
- Automatic scrolling applies only while `window._logPanelState.autoscroll` is enabled.
- Do not change log filtering, deduplication, or the 500-entry retention cap.
- Production code must be written only after a regression test fails for the current behavior.

---

### Task 1: Add regression tests for visual-top autoscroll

**Files:**
- Modify: `tests/js/components/logpanel.test.js` near the existing live insertion/order tests
- Reference: `src/local_deep_research/web/static/js/components/logpanel.js` functions `loadLogsForResearch` and `addLogEntryToPanel`

**Interfaces:**
- Consumes: `window.logPanel.addLog`, `window.logPanel.loadLogs`, and the existing `#console-log-container` test DOM.
- Produces: failing tests that require the container’s scroll position to be set to the visual top after live insertion and batch load.

- [ ] **Step 1: Write the live-insertion regression test**

Add a test that makes the container scrollable, inserts an existing entry, enables autoscroll, calls `logPanel.addLog`, flushes timers, and asserts the container is positioned at the visual top. The test must model the existing bug by making `scrollTop` writable and starting it away from the expected top:

```js
it('keeps the viewport at the visual top after a live log is added', async () => {
    const container = document.getElementById('console-log-container');
    Object.defineProperty(container, 'scrollHeight', {
        configurable: true,
        value: 800,
    });
    Object.defineProperty(container, 'clientHeight', {
        configurable: true,
        value: 200,
    });
    let scrollTop = 300;
    Object.defineProperty(container, 'scrollTop', {
        configurable: true,
        get: () => scrollTop,
        set: (value) => { scrollTop = value; },
    });

    window._logPanelState.expanded = true;
    window._logPanelState.autoscroll = true;
    logPanel.addLog('latest live log', 'info');
    await vi.runAllTimersAsync();

    expect(container.scrollTop).toBe(0);
});
```

- [ ] **Step 2: Run the focused test and verify it fails for the current implementation**

Run:

```bash
npx vitest run tests/js/components/logpanel.test.js -t "keeps the viewport at the visual top after a live log is added"
```

Expected: FAIL because the current `setTimeout(... consoleLogContainer.scrollTop = 0)` path does not reliably represent the visual-top position under the reversed flex scroll model in the jsdom regression setup.

- [ ] **Step 3: Add the batch-load regression test**

Add a test that returns one historical log from `fetch`, defines the same scrollable container properties, calls `logPanel.loadLogs`, and asserts the post-load position is the visual top:

```js
it('positions the viewport at the visual top after batch loading logs', async () => {
    const container = document.getElementById('console-log-container');
    Object.defineProperty(container, 'scrollHeight', {
        configurable: true,
        value: 800,
    });
    Object.defineProperty(container, 'clientHeight', {
        configurable: true,
        value: 200,
    });
    let scrollTop = 300;
    Object.defineProperty(container, 'scrollTop', {
        configurable: true,
        get: () => scrollTop,
        set: (value) => { scrollTop = value; },
    });
    globalThis.fetch = vi.fn(() => Promise.resolve({
        json: () => Promise.resolve([
            {
                timestamp: '2026-07-24T12:00:00.000Z',
                message: 'loaded log',
                log_type: 'info',
            },
        ]),
    }));

    await logPanel.loadLogs('batch-scroll-research');

    expect(container.scrollTop).toBe(0);
});
```

- [ ] **Step 4: Run both focused tests and confirm the failures are behavior failures, not setup errors**

Run:

```bash
npx vitest run tests/js/components/logpanel.test.js -t "visual top|batch loading"
```

Expected: the new assertions fail against the current implementation while the test file loads successfully.

### Task 2: Implement one centralized visual-top scroll helper

**Files:**
- Modify: `src/local_deep_research/web/static/js/components/logpanel.js` near `toggleAutoscroll`, `loadLogsForResearch`, and `addLogEntryToPanel`

**Interfaces:**
- Consumes: `#console-log-container` and `window._logPanelState.autoscroll`.
- Produces: an internal helper such as `scrollLogContainerToLatest()` that sets `scrollTop = 0` for the reversed layout only when autoscroll is enabled.

- [ ] **Step 1: Add the minimal helper**

Add this helper near `toggleAutoscroll`:

```js
function scrollLogContainerToLatest() {
    if (!window._logPanelState.autoscroll) {
        return;
    }

    const consoleLogContainer = document.getElementById('console-log-container');
    if (consoleLogContainer) {
        // column-reverse renders the newest DOM tail at visual top.
        consoleLogContainer.scrollTop = 0;
    }
}
```

- [ ] **Step 2: Replace duplicated live-scroll code**

In `addLogEntryToPanel`, replace the inline `setTimeout` block with:

```js
if (incrementCounter && element) {
    setTimeout(scrollLogContainerToLatest, 0);
}
```

The caller remains gated by `window._logPanelState.autoscroll` through the helper, so disabled autoscroll preserves the user’s current position.

- [ ] **Step 3: Scroll after successful batch rendering**

After the batch fragment is appended and the retention prune completes in `loadLogsForResearch`, call:

```js
scrollLogContainerToLatest();
```

Do not call it on empty/error responses, and do not change the merge path’s existing per-entry behavior.

- [ ] **Step 4: Keep toggle behavior on the same helper**

In `toggleAutoscroll`, replace the direct `consoleLogContainer.scrollTop = 0` assignment with `scrollLogContainerToLatest()` after applying the selected state. This keeps one source of truth for the reversed-layout scroll convention.

- [ ] **Step 5: Run the focused regression tests**

Run:

```bash
npx vitest run tests/js/components/logpanel.test.js -t "visual top|batch loading"
```

Expected: PASS.

### Task 3: Verify the complete log-panel regression suite

**Files:**
- No additional files.

- [ ] **Step 1: Run the full log-panel test file**

Run:

```bash
npx vitest run tests/js/components/logpanel.test.js
```

Expected: all log-panel tests pass, including ordering, deduplication, retention, initialization, and toggle behavior.

- [ ] **Step 2: Inspect the diff for scope and style**

Run:

```bash
git diff -- src/local_deep_research/web/static/js/components/logpanel.js tests/js/components/logpanel.test.js
```

Expected: only the helper/call-site changes and the two focused regression tests are present; no unrelated formatting or behavior changes.

- [ ] **Step 3: Run the broader JavaScript test suite**

Run:

```bash
npm test -- --run
```

Expected: the repository’s JavaScript tests pass. If unrelated pre-existing failures occur, report their exact output rather than modifying unrelated code.

- [ ] **Step 4: Record verification results**

Document the focused and full test commands and their outcomes in the final response. Do not claim the UI bug is fixed unless the focused regression tests and the complete log-panel suite pass.
