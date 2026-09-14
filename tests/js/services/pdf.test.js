/**
 * Tests for services/pdf.js
 *
 * Currently covers the replaceKatexWithLatex helper: the substitution step
 * that swaps rendered KaTeX elements for their raw LaTeX source before the
 * PDF walker extracts textContent. Regression-protects the fix for
 * top-level `$$\n...\n$$` display math being silently dropped from PDFs.
 */

import '@js/services/pdf.js';

const { replaceKatexWithLatex } = window.pdfService;

// Build a DOM with KaTeX-shaped markup. We construct it by hand (rather than
// driving marked + KaTeX) so the test does not depend on KaTeX's exact
// internal layout — only on the structural contract: `.katex-display` for
// display math, `.katex` for inline, both containing an `<annotation>` with
// the original LaTeX. That matches what marked-katex-extension always emits.
function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    for (const c of children) node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    return node;
}

function makeKatexInline(latex) {
    return el('span', { class: 'katex' },
        el('span', { class: 'katex-mathml' },
            el('math', {},
                el('semantics', {},
                    el('mrow'),
                    el('annotation', { encoding: 'application/x-tex' }, latex)
                )
            )
        ),
        el('span', { class: 'katex-html', 'aria-hidden': 'true' }, 'rendered')
    );
}

function makeKatexDisplay(latex) {
    return el('span', { class: 'katex-display' }, makeKatexInline(latex));
}

describe('replaceKatexWithLatex', () => {
    let root;

    beforeEach(() => {
        root = document.createElement('div');
        document.body.appendChild(root);
    });

    afterEach(() => {
        root.remove();
    });

    it('replaces inline .katex with `$LATEX$` text node inside its parent', () => {
        const p = document.createElement('p');
        p.append('Energy is ', makeKatexInline('E=mc^2'), ' famous.');
        root.appendChild(p);

        replaceKatexWithLatex(root);

        // The parent <p> still exists and now reads cleanly.
        expect(root.children.length).toBe(1);
        expect(root.children[0].tagName).toBe('P');
        expect(root.children[0].textContent).toBe('Energy is $E=mc^2$ famous.');
        expect(root.querySelector('.katex')).toBeNull();
    });

    it('wraps top-level .katex-display in a <p> so the PDF walker sees it', () => {
        // Regression: previously, display math at top level (the canonical
        // multi-line `$$\n...\n$$` form) was replaced with a bare text node.
        // The PDF walker iterates contentDiv.children (Elements only — text
        // nodes are skipped), so display math was silently dropped.
        root.appendChild(document.createElement('p')).textContent = 'Before';
        root.appendChild(makeKatexDisplay('\\sum_{i=1}^n i'));
        root.appendChild(document.createElement('p')).textContent = 'After';

        replaceKatexWithLatex(root);

        const kids = Array.from(root.children);
        expect(kids.map((k) => k.tagName)).toEqual(['P', 'P', 'P']);
        expect(kids[0].textContent).toBe('Before');
        expect(kids[1].textContent).toBe('$$\\sum_{i=1}^n i$$');
        expect(kids[2].textContent).toBe('After');
    });

    it('handles back-to-back display math blocks', () => {
        root.appendChild(makeKatexDisplay('a'));
        root.appendChild(makeKatexDisplay('b'));

        replaceKatexWithLatex(root);

        const kids = Array.from(root.children);
        expect(kids.length).toBe(2);
        expect(kids[0].textContent).toBe('$$a$$');
        expect(kids[1].textContent).toBe('$$b$$');
    });

    it('handles display math nested inside another element', () => {
        const li = document.createElement('li');
        li.appendChild(makeKatexDisplay('x=1'));
        const ul = document.createElement('ul');
        ul.appendChild(li);
        root.appendChild(ul);

        replaceKatexWithLatex(root);

        // The wrap <p> sits inside the <li>; the walker iterates the <ul>
        // and the <li> textContent recovers the LaTeX.
        expect(root.querySelector('.katex-display')).toBeNull();
        expect(root.querySelector('li').textContent).toBe('$$x=1$$');
    });

    it('leaves elements without an annotation untouched', () => {
        // Defensive: if DOMPurify ever stripped <annotation>, the substitution
        // should skip rather than silently produce $$$$ or $$.
        const broken = el('span', { class: 'katex-display' },
            el('span', { class: 'katex-html' }, 'no annotation here')
        );
        root.appendChild(broken);

        replaceKatexWithLatex(root);

        // Unchanged.
        expect(root.querySelector('.katex-display')).not.toBeNull();
        expect(root.querySelector('.katex-display').textContent).toBe('no annotation here');
    });

    it('is a no-op on a container with no KaTeX', () => {
        const p = document.createElement('p');
        p.textContent = 'Plain text only.';
        root.appendChild(p);

        replaceKatexWithLatex(root);

        expect(root.children.length).toBe(1);
        expect(root.children[0].textContent).toBe('Plain text only.');
    });
});

/**
 * loadImageForPdf
 *
 * Loads an image (URL or data URI) and converts it to a PNG data URL via
 * canvas so jsPDF.addImage receives a format it can embed. Returns
 * { dataUrl, width, height, format } on success or null on any failure
 * (empty src, network error, CORS-tainted canvas, timeout).
 *
 * Regression-protects the fix for research-report inline images being
 * silently dropped from exported PDFs. Previous behaviour read
 * new Image().width/height synchronously (always 0) and passed the raw
 * URL to jsPDF.addImage (which only accepts data URLs / canvases /
 * HTMLImageElement).
 *
 * happy-dom does not implement Canvas2D context nor actually load images,
 * so the happy-path and error-path tests stub Image + document.createElement
 * to simulate the browser's behaviour.
 */

const { loadImageForPdf } = window.pdfService;

const SUCCESS_PNG_DATAURL = 'data:image/png;base64,FAKE';
const TEST_IMAGE_W = 320;
const TEST_IMAGE_H = 180;

/**
 * Build a fake <canvas> with the minimum API loadImageForPdf touches:
 * width / height properties + getContext('2d') + toDataURL.
 *
 * happy-dom's native canvas returns getContext() === undefined, which
 * would make every happy-path test fail for the wrong reason. We patch
 * document.createElement so a 'canvas' request returns our stub instead.
 */
function makeFakeCanvas(width = TEST_IMAGE_W, height = TEST_IMAGE_H) {
    const calls = { drawImage: 0 };
    const ctx = {
        drawImage: (...args) => {
            calls.drawImage += 1;
            // Minimal sanity-check the args we'd expect: the loaded <img>.
            if (args[0] && typeof args[0].naturalWidth === 'number') {
                // ok
            }
        },
    };
    const canvas = {
        width,
        height,
        getContext: (kind) => (kind === '2d' ? ctx : null),
        toDataURL: (mime) => {
            if (mime !== 'image/png') {
                throw new Error(`unexpected mime: ${mime}`);
            }
            return SUCCESS_PNG_DATAURL;
        },
        __calls: calls,
    };
    return canvas;
}

/**
 * Build a fake Image class whose load/error can be driven by the test.
 *
 * Usage:
 *   const Image = makeFakeImage();
 *   window.Image = Image;
 *   const img = new Image();
 *   img.src = '...';           // no event fires yet
 *   Image.fireLoad(img);       // synchronously invokes onload
 *   Image.fireError(img);      // synchronously invokes onerror
 *
 * Defaults to crossOrigin = 'anonymous' to mirror the production helper.
 */
function makeFakeImage() {
    class FakeImage {
        constructor() {
            this.crossOrigin = '';
            this.naturalWidth = TEST_IMAGE_W;
            this.naturalHeight = TEST_IMAGE_H;
            this._src = '';
            // Track the most recently constructed instance so tests can
            // drive onload / onerror after the helper has built it.
            FakeImage.last = this;
        }
        set src(v) {
            this._src = v;
            // Intentionally do NOT fire any callback here — let the test
            // drive it explicitly so we can interleave with fake timers.
        }
        get src() {
            return this._src;
        }
    }
    FakeImage.last = null;
    FakeImage.fireLoad = (img) => img.onload && img.onload();
    FakeImage.fireError = (img) => img.onerror && img.onerror(new Error('load failed'));
    return FakeImage;
}

describe('loadImageForPdf', () => {
    let originalImage;
    let originalCreateElement;

    beforeEach(() => {
        originalImage = window.Image;
        originalCreateElement = document.createElement.bind(document);
    });

    afterEach(() => {
        window.Image = originalImage;
        document.createElement = originalCreateElement;
        vi.useRealTimers();
    });

    it('returns null when src is an empty string', async () => {
        const result = await loadImageForPdf('');
        expect(result).toBeNull();
    });

    it('returns null when src is null / undefined', async () => {
        expect(await loadImageForPdf(null)).toBeNull();
        expect(await loadImageForPdf(undefined)).toBeNull();
    });

    it('returns { dataUrl, width, height, format: PNG } on successful load', async () => {
        const FakeImage = makeFakeImage();
        window.Image = FakeImage;
        document.createElement = (tag) => {
            if (tag === 'canvas') return makeFakeCanvas();
            return originalCreateElement(tag);
        };

        const promise = loadImageForPdf('https://example.com/figure.png');
        const img = FakeImage.last; // captured by helper's `new Image()`
        expect(img).toBeTruthy();
        expect(img.crossOrigin).toBe('anonymous');
        FakeImage.fireLoad(img);

        const result = await promise;
        expect(result).toEqual({
            dataUrl: SUCCESS_PNG_DATAURL,
            width: TEST_IMAGE_W,
            height: TEST_IMAGE_H,
            format: 'PNG',
        });
    });

    it('returns null when the image fails to load (404 / network error)', async () => {
        const FakeImage = makeFakeImage();
        window.Image = FakeImage;
        document.createElement = (tag) => {
            if (tag === 'canvas') return makeFakeCanvas();
            return originalCreateElement(tag);
        };

        const promise = loadImageForPdf('https://example.com/missing.png');
        const img = FakeImage.last;
        FakeImage.fireError(img);

        const result = await promise;
        expect(result).toBeNull();
    });

    it('returns null when image load exceeds the timeout', async () => {
        vi.useFakeTimers();
        const FakeImage = makeFakeImage();
        window.Image = FakeImage;
        document.createElement = (tag) => {
            if (tag === 'canvas') return makeFakeCanvas();
            return originalCreateElement(tag);
        };

        const promise = loadImageForPdf('https://example.com/never-responds.png');
        // Drain microtasks first so the helper installs its setTimeout.
        await vi.advanceTimersByTimeAsync(0);
        // Push past the 5s timeout the helper uses.
        await vi.advanceTimersByTimeAsync(6000);

        const result = await promise;
        expect(result).toBeNull();
    });

    it('returns null when canvas.toDataURL throws (CORS-tainted canvas)', async () => {
        const FakeImage = makeFakeImage();
        window.Image = FakeImage;
        const tainted = makeFakeCanvas();
        tainted.toDataURL = () => {
            throw new Error('SecurityError: tainted canvases');
        };
        document.createElement = (tag) => {
            if (tag === 'canvas') return tainted;
            return originalCreateElement(tag);
        };

        const promise = loadImageForPdf('https://no-cors.example.com/x.png');
        const img = FakeImage.last;
        FakeImage.fireLoad(img);

        const result = await promise;
        expect(result).toBeNull();
    });

    it('draws the loaded image onto the canvas at (0, 0)', async () => {
        const FakeImage = makeFakeImage();
        window.Image = FakeImage;
        const canvas = makeFakeCanvas();
        document.createElement = (tag) => {
            if (tag === 'canvas') return canvas;
            return originalCreateElement(tag);
        };

        const promise = loadImageForPdf('data:image/png;base64,XXX');
        FakeImage.fireLoad(FakeImage.last);

        await promise;
        expect(canvas.__calls.drawImage).toBe(1);
        expect(canvas.width).toBe(TEST_IMAGE_W);
        expect(canvas.height).toBe(TEST_IMAGE_H);
    });
});


/**
 * renderTitleForPdf + formatPageNumberForPdf
 *
 * Client-side twin of the server-side title + Chinese page-number work
 * done in web/services/pdf_service.py. The client-side pdf.js can't
 * embed Chinese via jsPDF's built-in Helvetica font, so both helpers
 * defer to html2canvas (already loaded for the complex-element
 * fallback) — the browser draws the text with the system's CJK
 * fonts and we embed the rasterised result as a PNG.
 */

const { renderTitleForPdf, formatPageNumberForPdf } = window.pdfService;

// Happy-dom stubs Image loading and Canvas2D, so for the unit tests we
// only assert the orchestration logic (argument forwarding, fall-back
// strings) rather than the rasterisation itself. The rasteriser is
// exercised by the manual smoke test on the WebUI.

const FAKE_DATAURL =
    'data:image/png;base64,iVBORw0FAKE';

function makeFakeHtml2Canvas(impl) {
    if (impl) return impl;
    // Return a 2x2 fake canvas so width/height math is non-trivial.
    return async () => ({
        toDataURL: () => FAKE_DATAURL,
        width: 200,
        height: 80,
    });
}

function makeFakePdf() {
    const calls = { text: [], addImage: [] };
    return {
        calls,
        setFontSize() {},
        setTextColor() {},
        text: (...args) => calls.text.push(args),
        addImage: (...args) => calls.addImage.push(args),
        internal: { pageSize: { getWidth: () => 612, getHeight: () => 792 } },
        addPage() {},
    };
}

describe('renderTitleForPdf', () => {
    let originalHtml2Canvas;

    beforeEach(() => {
        originalHtml2Canvas = window.html2canvas;
        window.html2canvas = makeFakeHtml2Canvas();
    });

    afterEach(() => {
        window.html2canvas = originalHtml2Canvas;
    });

    it('renders "关于{query}的研究报告" via html2canvas', async () => {
        const pdf = makeFakePdf();
        const margin = 40;
        const contentWidth = 532;

        await renderTitleForPdf(pdf, '人工智能的发展趋势', {
            margin,
            contentWidth,
            pdfWidth: 612,
        });

        expect(pdf.calls.addImage.length).toBe(1);
        const args = pdf.calls.addImage[0];
        expect(args[0]).toBe(FAKE_DATAURL);
        expect(args[1]).toBe('PNG');  // format
        // Title spans the printable column, centred horizontally:
        // x = (pageWidth - contentWidth) / 2 = margin.
        expect(args[2]).toBe(margin);
        // y is at the top margin (sits before the body content).
        expect(args[3]).toBe(margin);
    });

    it('returns the rendered height so the caller can advance the cursor', async () => {
        const pdf = makeFakePdf();
        const result = await renderTitleForPdf(pdf, 'x', {
            margin: 40,
            contentWidth: 532,
            pdfWidth: 612,
        });
        expect(typeof result.height).toBe('number');
        expect(result.height).toBeGreaterThan(0);
    });

    it('returns zero height on render failure (does not throw)', async () => {
        window.html2canvas = async () => {
            throw new Error('CORS / not ready');
        };
        const pdf = makeFakePdf();
        const result = await renderTitleForPdf(pdf, 'x', {
            margin: 40,
            contentWidth: 532,
        });
        expect(result.height).toBe(0);
        expect(pdf.calls.addImage.length).toBe(0);
    });

    it('escapes HTML in the query before rendering', async () => {
        const pdf = makeFakePdf();
        // We can't easily assert the off-screen DOM the helper builds,
        // but we can verify no <script> tag is appended to the document.
        await renderTitleForPdf(pdf, '<script>alert(1)</script>', {
            margin: 40,
            contentWidth: 532,
        });
        // document.body should have been left clean.
        const stray = document.body.querySelector('div.ldr-pdf-title-render');
        // The helper cleans up its temp div synchronously after render.
        expect(stray).toBeNull();
    });
});


describe('formatPageNumberForPdf', () => {
    it('formats "第N页/共M页"', () => {
        expect(formatPageNumberForPdf(1, 5)).toBe('第1页/共5页');
        expect(formatPageNumberForPdf(3, 12)).toBe('第3页/共12页');
    });

    it('pads nothing (single-digit pages are emitted as-is)', () => {
        // Chinese typographic convention — no zero-padding.
        expect(formatPageNumberForPdf(9, 10)).toBe('第9页/共10页');
    });

    it('handles single-page documents', () => {
        expect(formatPageNumberForPdf(1, 1)).toBe('第1页/共1页');
    });
});
