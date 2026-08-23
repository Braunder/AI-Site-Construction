"""Adversarial-проверка санитайзера codegen (breaker, не входит в CI)."""
from app.services.codegen import find_forbidden

tests = [
    ('<a href="jav\tascript:alert(1)">x</a>', "tab-in-js-scheme"),
    ('<a href="&#106;avascript:alert(1)">x</a>', "html-entity-js-scheme"),
    ('<img src="//evil.com/x.png">', "protocol-relative evil"),
    ('<img src="https://cdn.jsdelivr.net/npm/x.js">', "allowed cdn"),
    ('<script src="https://evil.com/s.js"></script>', "evil script"),
    ('<style>@import url("https://evil.com/e.css");</style>', "css import"),
    ('<div style="background:url(https://evil.com/i.png)">x</div>', "inline css url"),
    ("<iframe src='https://x.com'></iframe>", "iframe"),
    ('<meta http-equiv="refresh" content="0;url=x">', "meta refresh"),
    ('<svg><use href="https://evil.com/s.svg#x"/></svg>', "svg use"),
    ('<input type=image src="https://evil.com/i.png">', "input image"),
    ('<img srcset="https://evil.com/a.png 1x">', "srcset"),
    ('<base href="https://evil.com/">', "base tag"),
    ('<a href="jAvAsCrIpT:alert(1)">x</a>', "mixed case js scheme"),
    ('<video poster="https://evil.com/p.jpg"></video>', "video poster"),
    ('<picture><source srcset="https://evil.com/a.webp"></picture>', "picture source"),
]

fails = 0
for html, name in tests:
    problems = find_forbidden(html)
    status = "BLOCKED" if problems else "PASSED!!!"
    if not problems and name not in ("allowed cdn",):
        fails += 1
    print(f"{name:28s} {status} {problems}")
print("\nUNEXPECTED PASSES:", fails)
