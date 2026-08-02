"""The themes.css startup regeneration must degrade gracefully on a
read-only target.

The hot-mount deployment (docker-compose.ldr-local.yml) mounts the
package dir `:ro`, so startup's write of the combined themes.css raises
`OSError: [Errno 30] Read-only file system` (EROFS). The generator
already has a designed fallback for unwritable targets — the
"pre-generated" warning — but it only caught `PermissionError`
(errno 13, EACCES), so EROFS fell into the catch-all ERROR path.

Note: create_app() runs inside the test body (not a fixture), because
pytest's caplog clears records between fixture setup and the test call —
the startup warning must be emitted during the call phase to be captured.
"""

import errno
from pathlib import Path

import pytest

from local_deep_research.web.app_factory import create_app


def _raise_erofs_for_themes_css(real_write_text):
    """Return a write_text that fails EROFS only for the combined themes.css."""

    def patched(self, *args, **kwargs):
        if str(self).endswith("css/themes.css"):
            raise OSError(errno.EROFS, "Read-only file system")
        return real_write_text(self, *args, **kwargs)

    return patched


@pytest.fixture
def erofs_themes_env(temp_data_dir, monkeypatch, loguru_caplog):
    """Environment where the themes.css startup write fails EROFS."""
    monkeypatch.setenv("LDR_DATA_DIR", str(temp_data_dir))
    monkeypatch.setattr(Path, "write_text", _raise_erofs_for_themes_css(Path.write_text))


def test_themes_css_erofs_degrades_to_pre_generated_warning(
    erofs_themes_env, loguru_caplog
):
    """EROFS on the themes.css write must hit the designed fallback
    warning — not an ERROR traceback — and startup must still succeed."""
    create_app()

    assert "pre-generated" in loguru_caplog.text
    assert "Error generating combined themes.css" not in loguru_caplog.text
