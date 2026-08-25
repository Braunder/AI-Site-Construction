import html
import re
from urllib.parse import unquote

# --- Извлечение HTML из ответа LLM ---

_FENCE_RE = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_html(text: str) -> str | None:
    """Достаёт HTML из markdown-блока ```html ... ``` либо из «голого» ответа."""
    if not text:
        return None
    m = _FENCE_RE.search(text)
    if m:
        candidate = m.group(1).strip()
    else:
        candidate = text.strip()
        # отрезаем возможную прозу до начала документа
        lower = candidate.lower()
        for marker in ("<!doctype", "<html"):
            idx = lower.find(marker)
            if idx != -1:
                candidate = candidate[idx:]
                break
    # отрезаем текст после закрывающего </html>
    end = candidate.lower().rfind("</html>")
    if end != -1:
        candidate = candidate[: end + len("</html>")]
    return candidate or None


# --- Структурная валидация ---

MIN_HTML_LEN = 200
MAX_HTML_LEN = 2_000_000


def validate_html(html_text: str) -> list[str]:
    """Проверяет наличие обязательных частей документа. Возвращает список ошибок."""
    errors: list[str] = []
    lower = html_text.lower()
    if not (MIN_HTML_LEN <= len(html_text) <= MAX_HTML_LEN):
        errors.append(f"недопустимый размер HTML ({len(html_text)} символов)")
    if "<!doctype" not in lower:
        errors.append("отсутствует DOCTYPE")
    for tag in ("<html", "<head", "<body", "</html>"):
        if tag not in lower:
            errors.append(f"отсутствует {tag}")
    return errors


# --- Проверка безопасности ---

ALLOWED_EXTERNAL_HOSTS = (
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "unpkg.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.tailwindcss.com",
)


def _normalize_url(url: str) -> str:
    """Декодирует HTML-сущности и percent-encoding перед анализом URL."""
    return unquote(html.unescape(url))


def _host_from_url(url: str) -> str:
    """Извлекает нормализованный хост из //host/... или http(s)://host/... (без credentials и порта)."""
    normalized = _normalize_url(url)
    # protocol-relative или явная схема — отрезаем всё до //
    after_scheme = normalized.split("//", 1)[1]
    netloc = after_scheme.split("/", 1)[0]
    # credentials user:pass@
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]
    host = netloc.split(":", 1)[0]
    return host.lower()


def _is_allowed_host(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in ALLOWED_EXTERNAL_HOSTS)


# Inline event handlers: on*="..." / on*='...' / on*=value без кавычек.
# \x00-\x1f между 'on' и именем события отсекает обходы вида o\tnerror.
_EVENT_HANDLER_RE = re.compile(
    r"<[^>]*?(?<![\w-])o[\s\x00-\x1f]*n[\s\x00-\x1f]*[a-z]+[\s\x00-\x1f]*=", re.I
)

# CSS url(javascript:...) / url(data:text/html;...) — схема внутри url() не проверялась.
_CSS_DANGEROUS_URL_RE = re.compile(
    r"url\(\s*['\"]?\s*(?:j[\s\x00-\x1f]*a[\s\x00-\x1f]*v[\s\x00-\x1f]*a[\s\x00-\x1f]*s[\s\x00-\x1f]*c[\s\x00-\x1f]*r[\s\x00-\x1f]*i[\s\x00-\x1f]*p[\s\x00-\x1f]*t\s*:|d[\s\x00-\x1f]*a[\s\x00-\x1f]*t[\s\x00-\x1f]*a\s*:|expression\s*\()",
    re.I,
)

FORBIDDEN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"<\s*(iframe|object|embed|applet|frame|portal)\b", re.I), "запрещённый тег (iframe/object/embed/applet/frame/portal)"),
    (re.compile(r"<\s*meta[^>]+http-equiv\s*=\s*['\"]?refresh", re.I), "meta refresh"),
    (re.compile(r"<\s*base\b", re.I), "тег <base> запрещён"),
    (re.compile(r"(href|src|action)\s*=\s*['\"]?\s*javascript\s*:", re.I), "javascript:-ссылка"),
    (re.compile(r"(href|src)\s*=\s*['\"]?\s*data\s*:\s*text/html", re.I), "data:text/html URL"),
    (re.compile(r"<\s*script[^>]+\bsrc\s*=\s*['\"]\s*http://", re.I), "внешний скрипт по http://"),
]

# Схемы javascript:/data: могут содержать whitespace (\t, \n, \r) внутри имени схемы —
# браузеры нормализуют его и исполняют JS. Проверяем текст без всех управляющих символов.
_SCHEME_STRIP_RE = re.compile(r"[\s\x00-\x1f\x7f]+")
_JAVASCRIPT_SCHEME_RE = re.compile(r"(?:href|src|action)\s*=\s*['\"]?\s*j[\s\x00-\x1f]*a[\s\x00-\x1f]*v[\s\x00-\x1f]*a[\s\x00-\x1f]*s[\s\x00-\x1f]*c[\s\x00-\x1f]*r[\s\x00-\x1f]*i[\s\x00-\x1f]*p[\s\x00-\x1f]*t\s*:", re.I)
_DATA_HTML_SCHEME_RE = re.compile(r"(?:href|src)\s*=\s*['\"]?\s*d[\s\x00-\x1f]*a[\s\x00-\x1f]*t[\s\x00-\x1f]*a\s*:\s*text\s*/\s*html", re.I)

# Внешние ресурсы: script/link (стили/скрипты), медиа (img/audio/video/source/picture/input),
# а также SVG <use href="..."> / <image href="...">. Кавычки вокруг значения необязательны.
# (?<![-\w]) гарантирует, что мы не ловим data-src / aria-src / xlink:href как src/href.
_SRC_RE = re.compile(
    r"<\s*(script|link|img|audio|video|source|input)\b[^>]*?"
    r"(?<![-\w])(?:src|href)(?!\w)\s*=\s*(?:['\"]\s*)?"
    r"((?:https?:)?//[^'\">\s]+)",
    re.I,
)

_SRCSET_RE = re.compile(
    r"<\s*(source|picture|img)\b[^>]*?\bsrcset\s*=\s*(?:['\"]\s*)?"
    r"([^'\">\s]+(?:\s+[^'\">\s]+)*)",
    re.I,
)

_USE_RE = re.compile(
    r"<\s*use\b[^>]*?"
    r"(?<![-\w])(?:href|xlink:href)(?!\w)\s*=\s*(?:['\"]\s*)?"
    r"((?:https?:)?//[^'\">\s]+)",
    re.I,
)

_SVG_IMAGE_RE = re.compile(
    r"<\s*image\b[^>]*?"
    r"(?<![-\w])(?:href|xlink:href)(?!\w)\s*=\s*(?:['\"]\s*)?"
    r"((?:https?:)?//[^'\">\s]+)",
    re.I,
)

_INPUT_IMAGE_RE = re.compile(r"<\s*input\b[^>]*?\btype\s*=\s*(?:['\"]\s*)?image", re.I)
_INPUT_IMAGE_SRC_RE = re.compile(
    r"(?<![-\w])src(?!\w)\s*=\s*(?:['\"]\s*)?((?:https?:)?//[^'\">\s]+)", re.I
)

# CSS-ресурсы: @import url(...) и url(...) внутри <style> / inline style
_STYLE_BLOCK_RE = re.compile(r"<\s*style[^>]*>(.*?)</\s*style\s*>", re.I | re.S)
# style="..." или style='...' — значение до соответствующей закрывающей кавычки
# (внутри могут быть кавычки другого типа, напр. url("...") в одинарных).
_INLINE_STYLE_RE = re.compile(
    r"\bstyle\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^>\s]+))", re.I
)
_CSS_URL_RE = re.compile(
    r"(?:@import\s+url\(\s*['\"]?\s*|url\(\s*['\"]?\s*)((?:https?:)?//[^'\")\s]+)",
    re.I,
)


def _check_src_matches(html_text: str, problems: list[str]) -> None:
    for m in _SRC_RE.finditer(html_text):
        host = _host_from_url(m.group(2))
        if not _is_allowed_host(host):
            problems.append(f"внешний ресурс с неразрешённого хоста: {host}")


def _check_srcset_matches(html_text: str, problems: list[str]) -> None:
    for m in _SRCSET_RE.finditer(html_text):
        for url in re.findall(r"(?:https?:)?//[^\s,]+", m.group(2).lower()):
            host = _host_from_url(url)
            if not _is_allowed_host(host):
                problems.append(f"внешний ресурс с неразрешённого хоста: {host}")


def _check_use_matches(html_text: str, problems: list[str]) -> None:
    for m in _USE_RE.finditer(html_text):
        host = _host_from_url(m.group(1))
        if not _is_allowed_host(host):
            problems.append(f"внешний ресурс с неразрешённого хоста: {host}")


def _check_svg_image_matches(html_text: str, problems: list[str]) -> None:
    for m in _SVG_IMAGE_RE.finditer(html_text):
        host = _host_from_url(m.group(1))
        if not _is_allowed_host(host):
            problems.append(f"внешний ресурс с неразрешённого хоста: {host}")


def _check_input_image_matches(html_text: str, problems: list[str]) -> None:
    for m in _INPUT_IMAGE_RE.finditer(html_text):
        tag = m.group(0)
        src_m = _INPUT_IMAGE_SRC_RE.search(tag)
        if src_m:
            host = _host_from_url(src_m.group(1))
            if not _is_allowed_host(host):
                problems.append(f"внешний ресурс с неразрешённого хоста: {host}")


def _check_css_matches(html_text: str, problems: list[str]) -> None:
    css_chunks: list[str] = []
    for m in _STYLE_BLOCK_RE.finditer(html_text):
        css_chunks.append(m.group(1))
    for m in _INLINE_STYLE_RE.finditer(html_text):
        css_chunks.append(m.group(1) or m.group(2) or m.group(3) or "")
    for chunk in css_chunks:
        for m in _CSS_URL_RE.finditer(chunk):
            host = _host_from_url(m.group(1))
            if not _is_allowed_host(host):
                problems.append(f"внешний ресурс с неразрешённого хоста: {host}")


def find_forbidden(html_text: str) -> list[str]:
    """Ищет запрещённые теги/скрипты/ресурсы. Возвращает список нарушений (пустой = чисто)."""
    problems: list[str] = []
    # Декодируем сущности и percent-encoding перед проверкой схем javascript:/data:
    decoded = unquote(html.unescape(html_text))
    for pattern, label in FORBIDDEN_PATTERNS:
        if pattern.search(decoded):
            problems.append(label)
    # Дополнительная проверка схем с whitespace внутри (java\tscript:, jav\nascript:)
    # Inline event handlers (onerror=, onload= и т.п.) — с учётом whitespace-обходов.
    if _EVENT_HANDLER_RE.search(decoded) or _EVENT_HANDLER_RE.search(_SCHEME_STRIP_RE.sub("", decoded)):
        problems.append("inline event handler (on*)")
    if _CSS_DANGEROUS_URL_RE.search(decoded):
        problems.append("CSS url(javascript:/data:text/html)")
    stripped = _SCHEME_STRIP_RE.sub("", decoded)
    if _JAVASCRIPT_SCHEME_RE.search(stripped):
        problems.append("javascript:-ссылка")
    if _DATA_HTML_SCHEME_RE.search(stripped):
        problems.append("data:text/html URL")
    _check_src_matches(decoded, problems)
    _check_srcset_matches(decoded, problems)
    _check_use_matches(decoded, problems)
    _check_svg_image_matches(decoded, problems)
    _check_input_image_matches(decoded, problems)
    _check_css_matches(decoded, problems)
    return sorted(set(problems))


def check_generated_html(html_text: str) -> list[str]:
    """Полная автопроверка сгенерированного кода: структура + безопасность."""
    return validate_html(html_text) + find_forbidden(html_text)
