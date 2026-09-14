**PDF export no longer hangs on slow / unreachable images.** Both the
WebUI "Download PDF" path and the in-browser jsPDF fallback now bound
each external image fetch at 5 seconds and fall back to a 1×1
transparent PNG placeholder on failure, so a slow CDN cannot freeze the
whole PDF render and a missing image shows as an invisible gap instead
of a hard error.
