"""Движок точечных правок HTML: применение операций от LLM (tool calling).

Операции:
- replace_text      — замена уникального фрагмента
- set_css_property  — изменение свойства CSS-правила по селектору
- insert_before_end — вставка HTML перед закрывающим тегом элемента
- delete_element    — удаление элемента по селектору
- rewrite_full      — полная перезапись документа (fallback)

Все операции детерминированы: не тронутое остаётся байт-в-байт.
"""

import re
from dataclasses import dataclass, field


class PatchError(ValueError):
    """Операцию невозможно применить однозначно."""


@dataclass
class PatchResult:
    html: str
    applied: list[str] = field(default_factory=list)


# ── Утилиты поиска CSS-правил ──

_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)


def _selector_candidates(selector: str) -> list[str]:
    """Варианты подстрок для поиска правила по селектору из инструмента.

    Модель может прислать 'section#hero', 'div.card', '#hero', '.card' —
    а в CSS селектор может быть записан как '#hero', '.card', 'section.hero' и т.п.
    """
    selector = selector.strip()
    candidates = [selector]
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9]*)((?:[.#][\w-]+)+)$", selector)
    if m:
        tag, rest = m.group(1).lower(), m.group(2)
        # 'section#hero' -> также ищем '#hero'
        # 'div.card' -> также ищем '.card'
        candidates.append(rest)
        # 'section.card.big' -> также 'card' и 'big' по отдельности (последний шанс)
        for part in re.findall(r"[.#]([\w-]+)", rest):
            candidates.append(part)
    return [c for c in candidates if c]


def _find_rule_span(css: str, selector_substr: str) -> tuple[int, int] | None:
    """Находит span (начало селектора, конец }) правила по селектору.

    Пробует точные варианты из _selector_candidates, затем подстроку.
    """
    for candidate in _selector_candidates(selector_substr):
        for m in _CSS_RULE_RE.finditer(css):
            sel = m.group(1).lower()
            # Точное совпадение части селектора: '#hero' в 'section, #hero' или '#hero'
            if re.search(rf"(?:^|[\s,>+~])({re.escape(candidate)}(?![\w-]))", sel):
                return m.start(), m.end()
    # Fallback: подстрока (старое поведение)
    for m in _CSS_RULE_RE.finditer(css):
        if selector_substr.lower() in m.group(1).lower():
            return m.start(), m.end()
    return None


def _set_css_property_in_block(block: str, prop: str, value: str) -> str:
    """Меняет/добавляет свойство в теле правила между { и }."""
    pattern = re.compile(rf"({re.escape(prop)}\s*:\s*)([^;}}]+)", re.I)
    if pattern.search(block):
        return pattern.sub(lambda m: m.group(1) + value, block, count=1)
    stripped = block.rstrip()
    sep = "" if stripped.endswith((";", "{")) or not stripped else ";"
    return stripped + sep + f" {prop}: {value};"


# ── Операции ──

def op_replace_text(html: str, args: dict) -> str:
    find = args.get("find", "")
    replace = args.get("replace", "")
    if not find:
        raise PatchError("replace_text: пустой 'find'")
    count = html.count(find)
    if count != 1:
        raise PatchError(f"replace_text: фрагмент найден {count} раз(а), нужен ровно 1")
    return html.replace(find, replace, 1)


def op_set_css_property(html: str, args: dict) -> str:
    selector = args.get("selector", "")
    prop = args.get("property", "")
    value = args.get("value", "")
    if not (selector and prop):
        raise PatchError("set_css_property: нужны 'selector' и 'property'")

    # Ищем только внутри <style>...</style>
    style_re = re.compile(r"(<style[^>]*>)(.*?)(</style\s*>)", re.S | re.I)
    for sm in style_re.finditer(html):
        css = sm.group(2)
        span = _find_rule_span(css, selector)
        if span is None:
            continue
        start, end = span
        rule = css[start:end]
        brace_open = rule.index("{")
        new_rule = (
            rule[: brace_open + 1]
            + _set_css_property_in_block(rule[brace_open + 1 : -1], prop, value)
            + rule[-1:]
        )
        new_css = css[:start] + new_rule + css[end:]
        return html[: sm.start(2)] + new_css + html[sm.end(2) :]
    raise PatchError(f"set_css_property: правило для '{selector}' не найдено")


def op_insert_before_end(html: str, args: dict) -> str:
    selector = args.get("selector", "")
    snippet = args.get("html", "")
    if not (selector and snippet):
        raise PatchError("insert_before_end: нужны 'selector' и 'html'")

    # Простой случай: id="..." или класс-подобные маркеры ищем как открывающий тег,
    # затем ближайший закрывающий парный тег.
    tag_match = re.match(r"^([a-zA-Z][a-zA-Z0-9]*)#", selector)
    if tag_match:
        tag, el_id = tag_match.group(1).lower(), selector.split("#", 1)[1]
        open_re = re.compile(rf"<{tag}\b[^>]*\bid\s*=\s*[\"']{re.escape(el_id)}[\"'][^>]*>", re.I)
    else:
        tag = selector.lower()
        open_re = re.compile(rf"<{tag}\b[^>]*>", re.I)

    m = open_re.search(html)
    if not m:
        raise PatchError(f"insert_before_end: элемент '{selector}' не найден")
    close_tag = f"</{tag}>"
    close_idx = html.find(close_tag, m.end())
    if close_idx == -1:
        raise PatchError(f"insert_before_end: закрывающий </{tag}> не найден")
    return html[:close_idx] + snippet + html[close_idx:]


def op_delete_element(html: str, args: dict) -> str:
    selector = args.get("selector", "")
    if not selector:
        raise PatchError("delete_element: нужен 'selector'")

    tag_match = re.match(r"^([a-zA-Z][a-zA-Z0-9]*)#", selector)
    if tag_match:
        tag, el_id = tag_match.group(1).lower(), selector.split("#", 1)[1]
        open_re = re.compile(
            rf"<{tag}\b[^>]*\bid\s*=\s*[\"']{re.escape(el_id)}[\"'][^>]*>.*?</{tag}\s*>",
            re.S | re.I,
        )
    else:
        tag = selector.lower()
        open_re = re.compile(rf"<{tag}\b[^>]*>.*?</{tag}\s*>", re.S | re.I)

    matches = list(open_re.finditer(html))
    if len(matches) != 1:
        raise PatchError(f"delete_element: найдено {len(matches)} совпадений для '{selector}', нужен 1")
    m = matches[0]
    return html[: m.start()] + html[m.end() :]


def op_rewrite_full(html: str, args: dict) -> str:
    new_html = args.get("html", "")
    if "<!DOCTYPE" not in new_html.upper() or "</html>" not in new_html.lower():
        raise PatchError("rewrite_full: ожидается полный HTML-документ")
    return new_html


OPERATIONS = {
    "replace_text": op_replace_text,
    "set_css_property": op_set_css_property,
    "insert_before_end": op_insert_before_end,
    "delete_element": op_delete_element,
    "rewrite_full": op_rewrite_full,
}

MAX_OPERATIONS = 30


def apply_operations(html: str, operations: list[dict]) -> PatchResult:
    """Последовательно применяет операции. Любая ошибка — PatchError с номером операции."""
    applied: list[str] = []
    current = html
    for i, op in enumerate(operations, 1):
        name = op.get("name", "")
        fn = OPERATIONS.get(name)
        if fn is None:
            raise PatchError(f"операция #{i}: неизвестный инструмент '{name}'")
        try:
            current = fn(current, op.get("arguments", {}))
        except PatchError as exc:
            raise PatchError(f"операция #{i} ({name}): {exc}") from exc
        applied.append(name)
        if len(applied) > MAX_OPERATIONS:
            raise PatchError(f"слишком много операций (> {MAX_OPERATIONS})")
    return PatchResult(html=current, applied=applied)
