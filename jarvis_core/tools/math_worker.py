"""Isolierter SymPy-Rechner (eigener Prozess, hartes Zeitlimit durch den Aufrufer).

Liest JSON {"op","expr","var"?} von stdin, schreibt JSON {"ok","result"|"error"}.
Eingaben werden gegen eine Allowlist geprüft — kein eval beliebiger Namen."""
import json
import re
import sys

ALLOWED = {"sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh", "exp", "log", "ln",
           "sqrt", "cbrt", "pi", "E", "I", "oo", "Abs", "abs", "factorial", "binomial", "gcd", "lcm",
           "floor", "ceiling", "sign", "Rational", "Eq", "Matrix", "det", "root", "min", "max"}
IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def check(expr: str) -> None:
    if len(expr) > 400:
        raise ValueError("Ausdruck zu lang")
    if re.search(r"__|[;'\"`$@\\]|lambda|\.\s*[A-Za-z_]", expr):
        raise ValueError("Unzulässige Zeichen im Ausdruck")
    for name in IDENT.findall(expr):
        if name not in ALLOWED and len(name) > 1 and not re.fullmatch(r"[a-z]\d?|[a-z]_\d", name):
            raise ValueError(f"Unbekannter Name: {name}")
    if re.search(r"\*\*[^+\-]*\*\*", expr):
        raise ValueError("Verkettete Potenzen sind nicht erlaubt")
    for m in re.finditer(r"\*\*\s*\(?\s*(\d+)", expr):
        if int(m.group(1)) > 10000:
            raise ValueError("Exponent zu groß")


def main() -> None:
    req = json.load(sys.stdin)
    import sympy as sp
    from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication_application,
                                            parse_expr, standard_transformations)
    tr = standard_transformations + (implicit_multiplication_application, convert_xor)
    op = req.get("op", "evaluate")
    expr = str(req["expr"]).replace("^", "**").replace("ln(", "log(")
    local = {n: getattr(sp, n) for n in ALLOWED if hasattr(sp, n)}
    core = {n: getattr(sp, n) for n in ("Integer", "Float", "Rational", "Symbol", "Function", "Mul", "Add", "Pow", "S")}
    local.update({"abs": sp.Abs, "ln": sp.log})

    def parse(s: str):
        s = s.replace("^", "**")
        check(s)
        return parse_expr(s, local_dict=dict(local), global_dict=dict(core), transformations=tr)

    var = sp.Symbol(req.get("var") or "x")
    if op == "solve":
        if "=" in expr and "==" not in expr:
            left, right = expr.split("=", 1)
            e = parse(left) - parse(right)
        else:
            e = parse(expr)
        res = sp.solve(e, var)
    else:
        e = parse(expr)
        if op == "diff":
            res = sp.diff(e, var)
        elif op == "integrate":
            res = sp.integrate(e, var)
        elif op == "simplify":
            res = sp.simplify(e)
        elif op == "expand":
            res = sp.expand(e)
        elif op == "factor":
            res = sp.factor(e)
        elif op == "limit":
            res = sp.limit(e, var, parse(str(req.get("to", "0"))))
        else:
            res = sp.N(e, 12) if e.free_symbols == set() else sp.simplify(e)
            exact = sp.nsimplify(e) if e.free_symbols == set() else None
            if exact is not None and str(exact) != str(res):
                res = f"{exact} ≈ {res}"
    print(json.dumps({"ok": True, "result": str(res)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}))
