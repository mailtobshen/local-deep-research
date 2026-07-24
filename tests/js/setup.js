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
globalThis.i18n = {
  t: (key) => key,
  tf: (key) => key,
};
