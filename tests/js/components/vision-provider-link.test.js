/**
 * Regression tests for vision_provider_link.js's
 * window.refreshVisionModelList.
 *
 * Mirrors llm.model: the dropdown is now rendered by settings.js
 * using renderCustomDropdownHTML + setupCustomDropdowns, which owns
 * the input, hidden value, refresh button binding, and provider
 * filter. vision_provider_link.js only owns the network call:
 *
 *     window.refreshVisionModelList({
 *         provider, url, apiKey,
 *         onSuccess(models), onError(err)
 *     })
 *
 * Strategy: read the IIFE source, extract the function body, run it
 * against a stubbed global `fetch`. We avoid extracting the whole
 * IIFE — that would require stubbing globals that aren't relevant —
 * and instead target only the function we want to test.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC_PATH = resolve(
    __dirname,
    '../../../src/local_deep_research/web/static/js/components/vision_provider_link.js'
);
const SOURCE = readFileSync(SRC_PATH, 'utf8');

/**
 * Extract the refreshVisionModelList function body (the bytes between
 * the opening { and the matching closing }).
 */
function extractFnBody(source, fnName) {
    const marker = `function ${fnName}`;
    const start = source.indexOf(marker);
    if (start < 0) {
        throw new Error(`Could not find function ${fnName} in source.`);
    }
    let i = source.indexOf('{', start);
    if (i < 0) throw new Error(`No opening brace for ${fnName}.`);
    const openBrace = i;
    let depth = 0;
    for (; i < source.length; i++) {
        if (source[i] === '{') depth++;
        else if (source[i] === '}') {
            depth--;
            if (depth === 0) break;
        }
    }
    if (depth !== 0) throw new Error(`Brace mismatch in ${fnName}.`);
    return source.slice(openBrace + 1, i);
}

const REFRESH_BODY = extractFnBody(SOURCE, 'refreshVisionModelList');

/**
 * Run refreshVisionModelList with a stubbed `fetch`. The stub's
 * behavior is configured by the test:
 *
 *   fetchStub = { status, body, throw } — what the network returns.
 *
 * Returns a promise that resolves once onSuccess or onError has been
 * called (whichever fired).
 */
function runRefresh(fetchStub) {
    let onSuccessCalled = null;
    let onErrorCalled = null;

    const fakeFetch = function (url, opts) {
        if (fetchStub.throw) {
            return Promise.reject(fetchStub.throw);
        }
        return Promise.resolve({
            ok: fetchStub.status >= 200 && fetchStub.status < 300,
            status: fetchStub.status,
            json: function () {
                return Promise.resolve(fetchStub.body);
            },
        });
    };

    // Wrap the body so we explicitly pass `args` as a function
    // parameter (the production function uses the name `args` in its
    // signature, but after extraction the body has no signature of
    // its own — the parameter name has to be re-introduced here).
    // eslint-disable-next-line no-new-func
    const fn = new Function(
        'fetch',
        `function refreshVisionModelList(args) {\n${REFRESH_BODY}\n}\nreturn refreshVisionModelList;`
    );
    const refresh = fn(fakeFetch);

    const settled = new Promise((resolve) => {
        refresh({
            provider: 'ollama',
            url: 'http://localhost:11434',
            apiKey: '',
            onSuccess: (models) => {
                onSuccessCalled = models;
                resolve();
            },
            onError: (err) => {
                onErrorCalled = err;
                resolve();
            },
        });
    });
    return settled.then(() => ({ onSuccessCalled, onErrorCalled }));
}

describe('window.refreshVisionModelList', () => {
    it('invokes onSuccess with the models array on a successful response', async () => {
        const { onSuccessCalled, onErrorCalled } = await runRefresh({
            status: 200,
            body: {
                models: [
                    { value: 'llava', label: 'LLaVA', provider: 'ollama' },
                    { value: 'qwen3-vl:8b', label: 'Qwen3-VL 8B', provider: 'ollama' },
                ],
            },
        });
        expect(onErrorCalled).toBeNull();
        expect(onSuccessCalled).toEqual([
            { value: 'llava', label: 'LLaVA', provider: 'ollama' },
            { value: 'qwen3-vl:8b', label: 'Qwen3-VL 8B', provider: 'ollama' },
        ]);
    });

    it('invokes onSuccess with an empty array when the response has no models', async () => {
        const { onSuccessCalled, onErrorCalled } = await runRefresh({
            status: 200,
            body: { models: [] },
        });
        expect(onErrorCalled).toBeNull();
        expect(onSuccessCalled).toEqual([]);
    });

    it('invokes onSuccess with an empty array when the response is missing models key', async () => {
        const { onSuccessCalled, onErrorCalled } = await runRefresh({
            status: 200,
            body: {},
        });
        expect(onErrorCalled).toBeNull();
        expect(onSuccessCalled).toEqual([]);
    });

    it('invokes onError when the response status is not 2xx', async () => {
        const { onSuccessCalled, onErrorCalled } = await runRefresh({
            status: 502,
            body: { error: 'bad gateway' },
        });
        expect(onSuccessCalled).toBeNull();
        expect(onErrorCalled).toBeTruthy();
        expect(onErrorCalled.message).toContain('502');
    });

    it('invokes onError when fetch rejects (network down)', async () => {
        const { onSuccessCalled, onErrorCalled } = await runRefresh({
            throw: new Error('ECONNREFUSED'),
        });
        expect(onSuccessCalled).toBeNull();
        expect(onErrorCalled).toBeTruthy();
        expect(onErrorCalled.message).toBe('ECONNREFUSED');
    });

    it('is a no-op when provider or url is missing', async () => {
        let onSuccessCalled = null;
        let onErrorCalled = null;
        const fakeFetch = function () {
            throw new Error('fetch should not be called when inputs are missing');
        };
        // eslint-disable-next-line no-new-func
        const fn = new Function(
            'fetch',
            `function refreshVisionModelList(args) {\n${REFRESH_BODY}\n}\nreturn refreshVisionModelList;`
        );
        const refresh = fn(fakeFetch);
        await refresh({
            provider: '',
            url: '',
            apiKey: '',
            onSuccess: (m) => { onSuccessCalled = m; },
            onError: (e) => { onErrorCalled = e; },
        });
        expect(onSuccessCalled).toBeNull();
        expect(onErrorCalled).toBeNull();
    });
});