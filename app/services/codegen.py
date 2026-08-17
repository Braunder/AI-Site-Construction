import re

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


def validate_html(html: str) -> list[str]:
    """Проверяет наличие обязательных частей документа. Возвращает список ошибок."""
    errors: list[str] = []
    lower = html.lower()
    if not (MIN_HTML_LEN <= len(html) <= MAX_HTML_LEN):
        errors.append(f"недопустимый размер HTML ({len(html)} символов)")
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

FORBIDDEN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"<\s*(iframe|object|embed|applet)\b", re.I), "запрещённый тег (iframe/object/embed/applet)"),
    (re.compile(r"<\s*meta[^>]+http-equiv\s*=\s*['\"]?refresh", re.I), "meta refresh"),
    (re.compile(r"(href|src|action)\s*=\s*['\"]?\s*javascript\s*:", re.I), "javascript:-ссылка"),
    (re.compile(r"(href|src)\s*=\s*['\"]?\s*data\s*:\s*text/html", re.I), "data:text/html URL"),
    (re.compile(r"<\s*script[^>]+\bsrc\s*=\s*['\"]\s*http://", re.I), "внешний скрипт по http://"),
]

_SRC_RE = re.compile(r"<\s*(script|link)[^>]+\b(?:src|href)\s*=\s*['\"]\s*(https?://[^'\"\s>]+)", re.I)


def find_forbidden(html: str) -> list[str]:
    """Ищет запрещённые теги/скрипты. Возвращает список нарушений (пустой = чисто)."""
    problems: list[str] = []
    for pattern, label in FORBIDDEN_PATTERNS:
        if pattern.search(html):
            problems.append(label)
    for m in _SRC_RE.finditer(html):
        url = m.group(2).lower()
        host = url.split("//", 1)[1].split("/", 1)[0]
        if not any(host == h or host.endswith("." + h) for h in ALLOWED_EXTERNAL_HOSTS):
            problems.append(f"внешний ресурс с неразрешённого хоста: {host}")
    return sorted(set(problems))


def check_generated_html(html: str) -> list[str]:
    """Полная автопроверка сгенерированного кода: структура + безопасность."""
    return validate_html(html) + find_forbidden(html)
