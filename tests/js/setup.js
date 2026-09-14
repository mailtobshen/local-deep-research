/**
 * Global test setup for Vitest + happy-dom
 *
 * Stubs browser globals that the app code expects to exist
 * (e.g. SafeLogger, which is loaded as a <script> tag in production).
 */

// Minimal SafeLogger stub — tests can spy on these via vi.spyOn()
globalThis.SafeLogger = {
  log: () => {},
  warn: () => {},
  error: () => {},
  info: () => {},
  debug: () => {},
};

// Minimal i18n stub — tests can spy on these via vi.spyOn().
// Production code reads i18n.t(...) / i18n.tf(...) when rendering translated
// strings; without this stub, suites that import the components before the
// per-test beforeAll runs crash with "i18n is not defined".
//
// `tf` mirrors the production implementation in web/static/js/services/i18n.js
// (printf-style %s / %d / %j / %% placeholders are substituted from the
// positional args). Returning the key verbatim broke any test that exercised
// the substitution path (form-validation's minLength/maxLength), so we keep
// the stub aligned with production.
globalThis.i18n = {
  t: (key) => key,
  tf: (key, ...args) => {
    const text = key;
    let idx = 0;
    return text.replace(/%([sdj%])/g, (match, fmt) => {
      if (fmt === '%') return '%';
      if (idx >= args.length) return match;
      const val = args[idx++];
      if (fmt === 'd') return String(parseInt(val, 10));
      if (fmt === 'j') return JSON.stringify(val);
      return String(val);
    });
  },
};
