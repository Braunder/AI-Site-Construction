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


def test_forbidden_protocol_relative():
    assert find_forbidden("<script src='//evil.com/x.js'></script>")
    assert find_forbidden('<link rel="stylesheet" href="//evil.com/x.css">')
    assert find_forbidden("<script src='//cdn.jsdelivr.net/npm/x'></script>") == []


def test_whitelist_with_explicit_port():
    assert find_forbidden('<script src="https://cdn.jsdelivr.net:443/npm/x"></script>') == []
    assert find_forbidden('<script src="https://evil.com:443/x.js"></script>')


def test_extract_html_keeps_html_end_inside_string():
    html = (
        "<!DOCTYPE html><html><head><style>" + "x" * 200 + "</style></head>"
        '<body><script>var x = "</html>"</script></body></html>'
    )
    assert extract_html(html) == html


def test_forbidden_javascript_with_whitespace_in_scheme():
    """XSS-обход: whitespace внутри схемы javascript: должен детектиться."""
    cases = [
        '<a href="java\tscript:alert(1)">x</a>',
        '<a href="jav\nascript:alert(1)">x</a>',
        '<a href="jav&#x0A;ascript:alert(1)">x</a>',  # после unescape/декодирования
        '<a href="java\rscript:alert(1)">x</a>',
    ]
    for case in cases:
        assert find_forbidden(case), case


def test_forbidden_data_html_with_whitespace():
    assert find_forbidden('<a href="dat\ta:text/html,x">y</a>')
    assert find_forbidden('<iframe src="da ta:text/html"></iframe>') or find_forbidden(
        '<a href="da\nta:text/html">x</a>'
    )


def test_forbidden_inline_style_mixed_quotes():
    """Inline style в одинарных кавычках с url("...") внутри — внешний CSS-ресурс."""
    case = '<div style=\'background:url("https://evil.com/i.png")\'>x</div>'
    problems = find_forbidden(case)
    assert any("evil.com" in p for p in problems), problems


def test_forbidden_unquoted_attrs():
    assert find_forbidden('<script src=//evil.com/x.js></script>')
    assert find_forbidden('<link rel=stylesheet href=//evil.com/x.css>')
    assert find_forbidden('<script src=http://evil.com/x.js></script>')
    # разрешённые CDN с незакавыченным URL должны проходить
    assert find_forbidden('<script src=//cdn.jsdelivr.net/npm/x></script>') == []


def test_forbidden_entities_in_javascript_and_data():
    assert find_forbidden('<a href="javascript&colon;alert(1)">x</a>')
    assert find_forbidden('<a href="data&#58;text/html,<script>alert(1)</script>">x</a>')
    assert find_forbidden('<a href=javascript&colon;alert(1)>x</a>')


def test_forbidden_inline_event_handlers():
    """XSS: inline on*-обработчики должны детектиться."""
    cases = [
        '<img src=x onerror="alert(1)">',
        "<svg onload=alert(1)></svg>",
        '<body onmouseover="steal()">',
        '<div onclick=steal()>x</div>',
        '<img src=x o\tnerror=alert(1)>',  # whitespace-обход имени события
    ]
    for case in cases:
        problems = find_forbidden(case)
        assert any("event handler" in p for p in problems), (case, problems)


def test_allowed_handlers_not_flagged():
    """Слова, содержащие 'on' не как атрибут-событие, не должны ловиться."""
    ok = (
        '<a href="https://fonts.googleapis.com">конфигурация</a>'
        + VALID_HTML
    )
    assert find_forbidden(ok) == []


def test_forbidden_css_url_javascript_scheme():
    """CSS url(javascript:) и url(data:text/html) должны детектиться."""
    cases = [
        "<style>a{background:url(javascript:alert(1))}</style>",
        '<div style="background:url(&quot;javascript:alert(1)&quot;)">x</div>',
        "<style>body{background:url('data:text/html,<script>')}</style>",
    ]
    for case in cases:
        problems = find_forbidden(case)
        assert any("CSS url" in p for p in problems), (case, problems)


def test_forbidden_external_media():
    assert find_forbidden('<img src="https://evil.com/x.jpg">')
    assert find_forbidden('<audio src="https://evil.com/x.mp3"></audio>')
    assert find_forbidden('<video src="https://evil.com/x.mp4"></video>')
    assert find_forbidden('<source src="https://evil.com/x.mp4">')
    assert find_forbidden('<source srcset="https://evil.com/a.jpg 1x, https://evil.com/b.jpg 2x">')
    assert find_forbidden('<picture srcset="https://evil.com/a.jpg 1x"></picture>')


def test_forbidden_svg_use():
    assert find_forbidden('<svg><use href="https://evil.com/x.svg#icon"></use></svg>')
    assert find_forbidden('<svg><use xlink:href="https://evil.com/x.svg#icon"></use></svg>')


def test_forbidden_entity_and_percent_encoding():
    assert find_forbidden('<a href="j&#97;vascript&#58;alert(1)">x</a>')
    assert find_forbidden('<a href="javascript%3aalert(1)">x</a>')
    assert find_forbidden('<a href="javascript&#00058;alert(1)">x</a>')
    assert find_forbidden('<a href="javascript&#x03A;alert(1)">x</a>')
    assert find_forbidden('<a href="data&#58;text/html,<script>alert(1)</script>">x</a>')


def test_forbidden_url_credentials():
    assert find_forbidden('<script src="https://cdn.jsdelivr.net:pass@evil.com/x.js"></script>')
    assert find_forbidden('<img src="https://unpkg.com:user@evil.com/x.jpg">')


def test_forbidden_base_tag():
    assert find_forbidden('<base href="https://evil.com"><script src="/x.js"></script>')
    assert find_forbidden('<base href="https://evil.com"><img src="/x.jpg"></base>')


def test_forbidden_css_external():
    assert find_forbidden('<style>@import url("https://evil.com/style.css");</style>')
    assert find_forbidden('<style>body{background:url("https://evil.com/x.png")}</style>')
    assert find_forbidden('<style>@font-face{font-family:x;src:url(https://evil.com/x.woff2)}</style>')
    assert find_forbidden('<div style="background:url(https://evil.com/x.png)"></div>')
    assert find_forbidden('<div style=background:url(//evil.com/x.png)></div>')


def test_allowed_css_internal():
    assert find_forbidden('<style>body{background:#fff}</style>') == []
    assert find_forbidden('<div style="background:#fff"></div>') == []
    assert find_forbidden('<style>@import url("/local.css")</style>') == []


def test_no_false_positives_on_data_attrs():
    html = '<script data-src="https://evil.com/x.js" src="https://cdn.jsdelivr.net/npm/x"></script>'
    assert find_forbidden(html) == []
    assert find_forbidden('<div aria-src="https://evil.com/x.jpg"></div>') == []


def test_forbidden_media_variants():
    assert find_forbidden('<img srcset="https://evil.com/a.jpg 1x">')
    assert find_forbidden('<input type="image" src="https://evil.com/x.png">')
    assert find_forbidden('<svg><image href="https://evil.com/x.png"/></svg>')
    assert find_forbidden('<svg><image xlink:href="https://evil.com/x.png"/></svg>')


def test_forbidden_frame_portal():
    assert find_forbidden('<frame src="https://evil.com">')
    assert find_forbidden('<portal src="https://evil.com">')
