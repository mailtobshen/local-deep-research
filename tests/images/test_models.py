# tests/images/test_models.py
def test_image_model_columns():
    from local_deep_research.database.models import Image

    cols = {c.name for c in Image.__table__.columns}
    for required in {
        "id",
        "research_id",
        "original_url",
        "local_path",
        "local_route",
        "alt",
        "source_url",
        "source_title",
        "content_hash",
        "width",
        "height",
        "created_at",
    }:
        assert required in cols, required


def test_search_result_has_html_content():
    from local_deep_research.database.models import SearchResult

    cols = {c.name for c in SearchResult.__table__.columns}
    assert "html_content" in cols


def test_settings_registered():
    import json
    import os
    import local_deep_research

    pkg_dir = os.path.dirname(local_deep_research.__file__)
    path = os.path.join(pkg_dir, "defaults", "default_settings.json")
    with open(path) as f:
        d = json.load(f)
    assert "report.enable_images" in d
    assert d["report.enable_images"]["value"] is True
    assert "report.image_vision_model" in d
    assert d["report.image_vision_model"]["value"] == ""


def test_vision_model_dropdown_carries_provider_tags():
    """Regression for the '链接测试' HTTP 400 / 2013 bug.

    Earlier designs used either a literal 'openai-compatible' value or
    an internal '__custom__' sentinel — both caused the link test to
    forward non-model strings to the endpoint. The current design
    keeps every option as a real model name AND tags each with a
    'provider' so vision_provider_link.js can filter the dropdown by
    the new Vision Model Provider setting.
    """
    import json
    import os
    import local_deep_research

    pkg_dir = os.path.dirname(local_deep_research.__file__)
    path = os.path.join(pkg_dir, "defaults", "default_settings.json")
    with open(path) as f:
        d = json.load(f)

    options = d["report.image_vision_model"]["options"]
    forbidden = {"openai-compatible", "__custom__"}
    for opt in options:
        assert opt["value"] not in forbidden, (
            f"{opt['value']!r} must not appear in the Vision Model "
            f"dropdown. These sentinels caused the original 2013 bug "
            f"(literal sentinel sent as model name → HTTP 400)."
        )
        # Every option must carry a provider tag so the linkage JS
        # can filter by provider.
        assert opt.get("provider"), (
            f"vision model option {opt['value']!r} is missing a "
            f"'provider' tag — vision_provider_link.js cannot "
            f"filter the dropdown by provider without it."
        )


def test_vision_provider_setting_exists():
    """Per the four-param redesign, the vision settings exposed in
    the WebUI are:
        - report.image_vision_provider  (new — drives dispatch)
        - report.image_vision_model
        - report.image_vision_url
        - report.image_vision_api_key
    Any extra vision settings (e.g. a dedicated OpenAI-Compatible
    Endpoint URL) duplicate the existing fields and confuse users.
    """
    import json
    import os
    import local_deep_research

    pkg_dir = os.path.dirname(local_deep_research.__file__)
    path = os.path.join(pkg_dir, "defaults", "default_settings.json")
    with open(path) as f:
        d = json.load(f)

    vision_keys = sorted(k for k in d if k.startswith("report.image_vision"))
    expected = sorted(
        {
            "report.image_vision_provider",
            "report.image_vision_model",
            "report.image_vision_url",
            "report.image_vision_api_key",
        }
    )
    assert vision_keys == expected, (
        f"Vision settings should be exactly {expected}, got "
        f"{vision_keys}"
    )

    # The provider setting must be a select with the 5 expected
    # provider values.
    provider_options = d["report.image_vision_provider"]["options"]
    provider_values = {opt["value"] for opt in provider_options}
    assert provider_values == {
        "ollama",
        "openai",
        "anthropic",
        "google",
        "openai_endpoint",
    }, (
        f"Vision Model Provider dropdown must offer exactly these 5 "
        f"providers: ollama, openai, anthropic, google, "
        f"openai_endpoint. Got: {provider_values}"
    )


def test_vision_setting_tips_translated_to_zh():
    """All four vision settings must have a Chinese translation
    registered under the English description key in zh.json. The
    WebUI uses i18n.t(setting.description) to render the tip under
    each setting, and falls back to the English text when no zh
    entry matches — so missing translations make the WebUI silently
    show English even in 中文 mode (this is the bug fixed in e7c84632).
    """
    import json
    import os
    import local_deep_research

    pkg_dir = os.path.dirname(local_deep_research.__file__)
    defaults_path = os.path.join(
        pkg_dir, "defaults", "default_settings.json"
    )
    zh_path = os.path.join(
        pkg_dir, "web", "translations", "zh.json"
    )
    with open(defaults_path) as f:
        d = json.load(f)
    with open(zh_path) as f:
        zh = json.load(f)

    for key in (
        "report.image_vision_provider",
        "report.image_vision_model",
        "report.image_vision_url",
        "report.image_vision_api_key",
    ):
        english = d[key]["description"]
        assert english in zh, (
            f"{key} English description is missing from zh.json. "
            f"The WebUI will fall back to English even in 中文 mode. "
            f"Add an entry mapping the exact English text to a Chinese "
            f"translation. English was:\n  {english}"
        )
        translation = zh[english]
        assert translation.strip(), (
            f"{key} has an empty Chinese translation in zh.json"
        )
        assert translation != english, (
            f"{key} Chinese translation is identical to the English "
            f"source — copy-paste error?"
        )


def test_vision_provider_linkage_script_exists():
    """vision_provider_link.js must exist and expose
    setupVisionProviderLinkage, called from settings.js after every
    render. It must filter the model <select> by the current
    provider, pre-fill the URL field, and inject a refresh button
    that hits /api/vision/available-models with the current
    provider/url/api_key.
    """
    import os
    import re

    import local_deep_research

    pkg_dir = os.path.dirname(local_deep_research.__file__)
    js_path = os.path.join(
        pkg_dir,
        "web",
        "static",
        "js",
        "components",
        "vision_provider_link.js",
    )
    assert os.path.exists(js_path), (
        "vision_provider_link.js is missing — the Vision Model "
        "Provider ↔ Model + URL linkage won't run."
    )
    with open(js_path) as f:
        src = f.read()
    assert "window.setupVisionProviderLinkage" in src, (
        "vision_provider_link.js must expose "
        "window.setupVisionProviderLinkage so settings.js can call it."
    )
    assert re.search(
        r"data-provider|optProvider",
        src,
    ), (
        "vision_provider_link.js does not look at the data-provider "
        "attribute — it cannot filter the model dropdown by provider."
    )
    assert "PROVIDER_URL_DEFAULTS" in src, (
        "vision_provider_link.js has no URL pre-fill map; the URL "
        "field will not auto-populate when the provider changes."
    )
    # Refresh button: must inject a button next to the model <select>
    # and call /api/vision/available-models.
    assert re.search(
        r"vision-model-refresh-btn",
        src,
    ), (
        "vision_provider_link.js does not inject a refresh button "
        "with the .vision-model-refresh-btn class next to the "
        "Vision Model select."
    )
    assert "/api/vision/available-models" in src, (
        "vision_provider_link.js does not call "
        "/api/vision/available-models — the refresh button is wired "
        "but the actual fetch is missing."
    )


def test_settings_renders_data_provider_attribute():
    """The settings.js <select> renderer must forward each option's
    'provider' tag as a data-provider attribute on the <option>, so
    vision_provider_link.js can read it and filter the dropdown.
    """
    import os
    import re

    import local_deep_research

    pkg_dir = os.path.dirname(local_deep_research.__file__)
    js_path = os.path.join(
        pkg_dir,
        "web",
        "static",
        "js",
        "components",
        "settings.js",
    )
    with open(js_path) as f:
        src = f.read()
    assert re.search(
        r"option\.provider[\s\S]{0,200}data-provider",
        src,
    ), (
        "settings.js does not forward each option's 'provider' tag "
        "as a data-provider attribute on the rendered <option>. The "
        "vision_provider_link.js linkage cannot filter the dropdown "
        "by provider without this attribute."
    )


def test_vision_test_button_sends_provider():
    """vision_test_button.js must include the selected provider in
    the /api/vision/test_connection request body. The backend uses
    this to dispatch to the right chat-model implementation
    (Anthropic, Google, OpenAI, OpenAI-compatible, or Ollama).
    Without it, the backend always falls back to 'openai_endpoint'
    which fails for native Anthropic / Google endpoints.
    """
    import os
    import re

    import local_deep_research

    pkg_dir = os.path.dirname(local_deep_research.__file__)
    js_path = os.path.join(
        pkg_dir,
        "web",
        "static",
        "js",
        "components",
        "vision_test_button.js",
    )
    with open(js_path) as f:
        src = f.read()
    assert re.search(
        r"select\[name=['\"]report\.image_vision_provider['\"]\]",
        src,
    ), (
        "vision_test_button.js does not read "
        "select[name='report.image_vision_provider'] — the user's "
        "provider choice is never sent to the backend."
    )
    assert re.search(
        r"provider\s*:\s*provider",
        src,
    ), (
        "vision_test_button.js does not include 'provider' in the "
        "test_connection request body."
    )


def test_vision_test_button_label_is_chinese_connection_test():
    """The vision test button must be labelled '连接测试', not
    '链接测试'. The previous label was confusing because the button
    tests an end-to-end connection (URL reachability + auth + model
    availability), not a hyperlink check.
    """
    import os
    import re

    import local_deep_research

    pkg_dir = os.path.dirname(local_deep_research.__file__)
    js_path = os.path.join(
        pkg_dir,
        "web",
        "static",
        "js",
        "components",
        "vision_test_button.js",
    )
    with open(js_path) as f:
        src = f.read()
    assert "连接测试" in src, (
        "vision_test_button.js no longer renders '连接测试' as the "
        "button label. Without it the user sees an English / outdated "
        "label on a Chinese-locale WebUI."
    )
    assert "链接测试" not in src, (
        "vision_test_button.js still uses '链接测试' as a label. "
        "Replace with '连接测试'."
    )
