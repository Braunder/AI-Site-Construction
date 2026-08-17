from app.services.codegen import check_generated_html, extract_html, find_forbidden, validate_html

VALID_HTML = (
    "<!DOCTYPE html><html><head><title>t</title><style>body{color:#000}"
    + "x" * 200
    + "</style></head><body><h1>Hello</h1><script>console.log(1)</script></body></html>"
)


def test_extract_from_markdown_fence():
    raw = "Вот ваш сайт:\n```html\n" + VALID_HTML + "\n```\nГотово!"
    assert extract_html(raw) == VALID_HTML


def test_extract_bare_html_with_prose():
    raw = "Конечно, вот код:\n" + VALID_HTML + "\nНадеюсь, понравится"
    assert extract_html(raw) == VALID_HTML


def test_extract_returns_none_on_garbage():
    assert extract_html("") is None
    assert extract_html("просто текст без html") is not None  # вернёт как есть; отсеет validate
    assert validate_html(extract_html("просто текст без html"))  # ошибки есть


def test_validate_ok():
    assert validate_html(VALID_HTML) == []


def test_validate_missing_parts():
    errors = validate_html("<div>hi</div>")
    assert any("DOCTYPE" in e for e in errors)
    assert any("<body" in e for e in errors)


def test_forbidden_detects_dangerous():
    bad_cases = [
        "<iframe src='http://evil.com'></iframe>",
        "<a href='javascript:alert(1)'>x</a>",
        "<script src='http://evil.com/x.js'></script>",
        "<script src='https://evil.com/x.js'></script>",
        "<meta http-equiv='refresh' content='0;url=http://evil.com'>",
        "<object data='x.swf'></object>",
    ]
    for case in bad_cases:
        assert find_forbidden(case), case


def test_forbidden_allows_safe():
    safe = (
        VALID_HTML
        + "<link href='https://fonts.googleapis.com/css2?family=Inter' rel='stylesheet'>"
        + "<script src='https://cdn.jsdelivr.net/npm/bootstrap@5'></script>"
    )
    assert find_forbidden(safe) == []


def test_full_check():
    assert check_generated_html(VALID_HTML) == []
