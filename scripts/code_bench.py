"""Coding-Benchmark für Jarvis-Modelle: komplexe Aufgaben mit versteckten Tests.

Jede Lösung wird in einem eigenen Temp-Ordner mit dem Python der .venv ausgeführt
(sklearn/pandas vorhanden) und gegen versteckte Tests geprüft. Punkte = Anteil
bestandener Tests je Aufgabe. Zusätzlich werden Metriken (z. B. ROC-AUC des
Random Forest) und alle Rohantworten gespeichert, damit die Lösungen auch von
Hand beurteilt werden können.

    .venv/bin/python scripts/code_bench.py                         # alle Kandidaten, Thinking aus
    .venv/bin/python scripts/code_bench.py gemma4:26b --think on
    .venv/bin/python scripts/code_bench.py --only random_forest,sql_top_customers
    .venv/bin/python scripts/code_bench.py --check-reference DIR   # Tests gegen Referenzlösungen prüfen
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis_core.persona import messages_with_persona  # noqa: E402

HOST = "http://127.0.0.1:11434"
NUM_CTX = 16384
NUM_PREDICT = 8192          # großzügig: hier zählt Können, nicht Jarvis' Antwortbudget
GEN_TIMEOUT_S = 900
TEST_TIMEOUT_S = 240
PY = sys.executable

DEFAULT_MODELS = ["qwen3.6:35b", "qwen3.8:27b", "gemma4:26b", "lfm2.5:24b",
                  "llama3.3:70b-instruct-q3_K_M"]

HARNESS = r'''
import sys, json, re
sys.path.insert(0, ".")
RESULTS = []
METRICS = {}
SRC = open(SOLUTION_FILE, encoding="utf-8").read()
def check(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append([name, True, ""])
        except BaseException as e:  # noqa: BLE001
            RESULTS.append([name, False, (type(e).__name__ + ": " + str(e))[:240]])
        return fn
    return deco
def done():
    print("##RESULT##" + json.dumps({"results": RESULTS, "metrics": METRICS}))
'''

# ====================================================================== Aufgaben

RF_PROMPT = """\
Baue ein Churn-Vorhersagemodell mit scikit-learn. Schreibe ein Python-Modul mit genau diesen Funktionen:

1. `train(df: pd.DataFrame)` — bekommt die Trainingsdaten inkl. Zielspalte `gekuendigt` (0/1) und gibt ein
   fertig trainiertes Objekt zurück, das `predict(X)` und `predict_proba(X)` auf ROHEN DataFrames ohne
   Zielspalte unterstützt (also inkl. Vorverarbeitung, z. B. als sklearn-Pipeline).
2. `feature_importances(model) -> pd.Series` — Wichtigkeit je Merkmal NACH der Vorverarbeitung
   (Index = Feature-Namen, z. B. aus get_feature_names_out), absteigend sortiert.

Spalten: alter (int), einkommen (float, €/Monat), vertragsart ("monatlich" | "jährlich" | "zweijährig"),
region (Text), anrufe_support (int), nutzung_stunden (float), kunde_seit_monaten (int), gekuendigt (0/1).

Anforderungen:
- RandomForestClassifier als Modell, random_state=42.
- Fehlende Werte können in JEDER Merkmalsspalte vorkommen; im Betrieb tauchen auch neue, unbekannte Regionen auf.
- Die Klassen sind unausgeglichen (~17 % Kündiger) — berücksichtige das.
- Hyperparameter per Kreuzvalidierung (stratifiziert) optimieren, Zielmetrik ROC-AUC. Das Training darf
  höchstens 2 Minuten dauern.
- Das übergebene DataFrame darf nicht verändert werden.
Gib nur einen Python-Codeblock aus."""

RF_TESTS = r'''
import time
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score, f1_score

def make(n, seed, unseen=False):
    rng = np.random.default_rng(seed)
    vertrag = rng.choice(["monatlich", "jährlich", "zweijährig"], n, p=[.5, .3, .2])
    regions = ["Nord", "Süd", "West", "Ost"] + (["Zentral"] if unseen else [])
    df = pd.DataFrame({
        "alter": rng.integers(18, 80, n).astype(float),
        "einkommen": rng.normal(3500, 1200, n).clip(800),
        "vertragsart": vertrag,
        "region": rng.choice(regions, n),
        "anrufe_support": rng.poisson(1.5, n).astype(float),
        "nutzung_stunden": rng.gamma(2, 10, n),
        "kunde_seit_monaten": rng.integers(1, 72, n).astype(float),
    })
    z = (-1.6 + 1.7 * (vertrag == "monatlich") - 0.9 * (vertrag == "zweijährig")
         + 0.5 * df.anrufe_support - 0.045 * df.nutzung_stunden - 0.03 * df.kunde_seit_monaten
         + 0.0003 * (3000 - df.einkommen) + 0.4 * (df.region == "Ost"))
    y = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(int)
    for col, frac in [("alter", .08), ("einkommen", .10), ("vertragsart", .03), ("region", .03),
                      ("anrufe_support", .02), ("nutzung_stunden", .05), ("kunde_seit_monaten", .02)]:
        df.loc[rng.random(n) < frac, col] = np.nan
    df["gekuendigt"] = y
    return df

train_df = make(2500, 1)
test_df = make(1500, 2, unseen=True)
X_test, y_test = test_df.drop(columns="gekuendigt"), test_df["gekuendigt"]
snapshot = train_df.copy()
STATE = {}

@check("train() läuft fehlerfrei und < 150 s")
def _():
    t = time.time()
    STATE["model"] = solution.train(train_df)
    METRICS["train_s"] = round(time.time() - t, 1)
    assert METRICS["train_s"] < 150, f"{METRICS['train_s']} s"

@check("Eingabe-DataFrame unverändert")
def _():
    pd.testing.assert_frame_equal(train_df, snapshot)

@check("predict_proba auf Rohdaten mit NaN + unbekannter Region")
def _():
    p = STATE["model"].predict_proba(X_test)[:, 1]
    STATE["p"] = p
    assert len(p) == len(X_test) and np.isfinite(p).all()

@check("ROC-AUC ≥ 0.78 auf versteckten Testdaten")
def _():
    METRICS["roc_auc"] = round(float(roc_auc_score(y_test, STATE["p"])), 4)
    assert METRICS["roc_auc"] >= 0.78, METRICS["roc_auc"]

@check("F1 (Kündiger) ≥ 0.42 — Klassenungleichgewicht behandelt")
def _():
    pred = STATE["model"].predict(X_test)
    METRICS["f1"] = round(float(f1_score(y_test, pred)), 4)
    METRICS["pred_pos_rate"] = round(float(np.mean(pred)), 3)
    assert METRICS["f1"] >= 0.42, METRICS["f1"]   # Referenz: 0.49 mit, 0.25 ohne Klassengewichtung

@check("verwendet RandomForestClassifier")
def _():
    from sklearn.ensemble import RandomForestClassifier
    m = STATE["model"]
    inner = getattr(m, "best_estimator_", m)
    steps = getattr(inner, "steps", [("m", inner)])
    assert any(isinstance(s, RandomForestClassifier) for _, s in steps)

@check("Hyperparameter-Suche mit Kreuzvalidierung + ROC-AUC")
def _():
    assert re.search(r"GridSearchCV|RandomizedSearchCV|HalvingGridSearchCV|cross_val_score|cross_validate", SRC)
    assert re.search(r"roc_auc", SRC)
    assert re.search(r"StratifiedKFold|cv\s*=", SRC)

@check("feature_importances: Series, sortiert, Summe ≈ 1, sinnvolle Top-Merkmale")
def _():
    s = solution.feature_importances(STATE["model"])
    assert isinstance(s, pd.Series) and len(s) >= 7
    assert list(s.values) == sorted(s.values, reverse=True)
    assert abs(float(s.sum()) - 1) < 0.02, float(s.sum())
    top = " ".join(map(str, s.index[:4])).lower()
    METRICS["top_features"] = list(map(str, s.index[:4]))
    assert any(k in top for k in ("anrufe", "nutzung", "kunde_seit", "monatlich", "vertrag")), top
done()
'''

EXPR_PROMPT = """\
Schreibe in Python einen eigenen Parser und Auswerter für mathematische Ausdrücke:

    evaluate(expr: str, variables: dict[str, float] | None = None) -> float

Unterstützt werden müssen:
- Zahlen inkl. Dezimalzahlen und wissenschaftlicher Notation (3.5e2), beliebige Leerzeichen
- + - * / und ^ (Potenz, rechtsassoziativ: 2^3^2 = 512), Klammern
- unäres Minus/Plus; -2^2 ergibt -4 (Potenz bindet stärker als das Vorzeichen)
- Variablen aus `variables` sowie die Konstante pi
- Funktionen sqrt, sin, cos, abs, min, max (min/max mit beliebig vielen Argumenten)

Fehlerbehandlung: Syntaxfehler, unbekannte Namen/Funktionen und falsche Argumentzahl → ValueError;
Division durch null → ZeroDivisionError.
Verboten: eval, exec, compile, das Modul ast und Bibliotheken wie sympy — schreibe Tokenizer und
Parser selbst (z. B. rekursiver Abstieg). Gib nur einen Python-Codeblock aus."""

EXPR_TESTS = r'''
import math
E = solution.evaluate
def approx(a, b): assert abs(a - b) < 1e-9, (a, b)
def raises(exc, *args):
    try: E(*args)
    except exc: return
    except Exception as e: raise AssertionError(f"{type(e).__name__} statt {exc.__name__}")
    raise AssertionError(f"kein {exc.__name__}")

@check("Punkt vor Strich, Klammern")
def _(): approx(E("1 + 2 * 3"), 7); approx(E("(1 + 2) * 3"), 9); approx(E("2 * (3 + 4) - 5 / (1 + 1)"), 11.5)
@check("Linksassoziativität - und /")
def _(): approx(E("1 - 2 - 3"), -4); approx(E("8 / 2 / 2"), 2)
@check("Potenz rechtsassoziativ")
def _(): approx(E("2 ^ 3 ^ 2"), 512)
@check("unäres Minus: -2^2 = -4, --3 = 3, 2*-3 = -6")
def _(): approx(E("-2 ^ 2"), -4); approx(E("--3"), 3); approx(E("2 * -3"), -6); approx(E("+4"), 4)
@check("Dezimal + wissenschaftliche Notation")
def _(): approx(E("  3.5e2 / 7 "), 50); approx(E("0.5 + .25"), 0.75)
@check("Funktionen sqrt/max/min/abs/sin/cos")
def _():
    approx(E("sqrt(16) + max(1, 7, 3)"), 11); approx(E("min(4, 2, 9)"), 2)
    approx(E("abs(-3) + sin(0) + cos(0)"), 4); approx(E("max(2)"), 2)
@check("Variablen und pi")
def _(): approx(E("min(4, 2) * x", {"x": 5}), 10); approx(E("2 * pi"), 2 * math.pi); approx(E("a^2 + b", {"a": 3, "b": 1}), 10)
@check("verschachtelt")
def _(): approx(E("sqrt(max(9, (2 + 2) ^ 2)) * -(1 - 3)"), 8)
@check("Syntaxfehler → ValueError")
def _():
    for bad in ["1 +", "(1 + 2", "1 + 2)", "2 3", "", "* 3", "max(1,)", "3 $ 4"]:
        raises(ValueError, bad)
@check("unbekannte Namen/Funktionen → ValueError")
def _(): raises(ValueError, "y + 1"); raises(ValueError, "foo(1)"); raises(ValueError, "__import__('os')")
@check("falsche Argumentzahl → ValueError")
def _(): raises(ValueError, "sqrt(1, 2)"); raises(ValueError, "max()")
@check("Division durch null → ZeroDivisionError")
def _(): raises(ZeroDivisionError, "1 / 0"); raises(ZeroDivisionError, "1 / (2 - 2)")
@check("kein eval/exec/compile/ast/sympy")
def _():
    assert not re.search(r"(?<![\w.])(eval|exec|compile)\s*\(|\bimport\s+ast\b|\bfrom\s+ast\b|sympy", SRC)
done()
'''

CACHE_BUGGY = '''\
import time
from collections import OrderedDict


class TTLCache:
    """LRU-Cache mit fester Kapazität und Ablaufzeit (ttl Sekunden) pro Eintrag."""

    def __init__(self, capacity: int, ttl: float, clock=time.monotonic):
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self._data = OrderedDict()   # key -> (value, expires_at)

    def get(self, key, default=None):
        item = self._data.get(key)
        if item is None:
            return default
        value, expires_at = item
        if self.clock() > expires_at:
            del self._data[key]
            return default
        return value

    def put(self, key, value):
        if key in self._data:
            _, expires_at = self._data[key]
            self._data[key] = (value, expires_at)
            return
        if len(self._data) >= self.capacity:
            self._data.popitem(last=True)
        self._data[key] = (value, self.clock() + self.ttl)

    def __len__(self):
        return len(self._data)
'''

CACHE_PROMPT = f"""\
Diese TTLCache-Klasse hat mehrere Fehler. Gewünschtes Verhalten:
- Ein Eintrag ist genau ttl Sekunden gültig: zum Zeitpunkt put_zeit + ttl ist er bereits abgelaufen.
- get() eines gültigen Eintrags markiert ihn als zuletzt benutzt.
- put() eines vorhandenen Schlüssels ersetzt den Wert, setzt die Ablaufzeit neu und markiert ihn als zuletzt benutzt.
- Ist der Cache voll, werden zuerst abgelaufene Einträge entfernt; reicht das nicht, fliegt der am
  längsten nicht benutzte Eintrag (LRU).
- len() zählt nur nicht abgelaufene Einträge.
- capacity < 1 oder ttl <= 0 → ValueError im Konstruktor.
Finde und behebe alle Fehler, behalte die API (inkl. clock-Parameter) bei. Gib die vollständige korrigierte
Klasse in einem Python-Codeblock aus und nenne danach die gefundenen Fehler in kurzen Stichpunkten.

```python
{CACHE_BUGGY}```"""

CACHE_TESTS = r'''
class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
C = solution.TTLCache

@check("Grundfunktion put/get/default")
def _():
    c = C(2, 10, clock=Clock()); c.put("a", 1)
    assert c.get("a") == 1 and c.get("x") is None and c.get("x", 5) == 5
@check("Ablauf exakt bei put_zeit + ttl")
def _():
    k = Clock(); c = C(2, 10, clock=k); c.put("a", 1)
    k.t += 9.999; assert c.get("a") == 1
    k.t += 0.001; assert c.get("a") is None
@check("LRU: get markiert als zuletzt benutzt")
def _():
    c = C(2, 100, clock=Clock()); c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1 and c.get("c") == 3
@check("LRU: ältester Eintrag wird verdrängt (nicht der neueste)")
def _():
    c = C(2, 100, clock=Clock()); c.put("a", 1); c.put("b", 2); c.put("c", 3)
    assert c.get("a") is None and c.get("b") == 2 and c.get("c") == 3
@check("put auf vorhandenen Schlüssel erneuert TTL")
def _():
    k = Clock(); c = C(2, 10, clock=k); c.put("a", 1); k.t += 8; c.put("a", 2); k.t += 8
    assert c.get("a") == 2
@check("put auf vorhandenen Schlüssel markiert als zuletzt benutzt")
def _():
    c = C(2, 100, clock=Clock()); c.put("a", 1); c.put("b", 2); c.put("a", 3); c.put("c", 4)
    assert c.get("a") == 3 and c.get("b") is None
@check("voll: abgelaufene Einträge werden vor LRU entfernt")
def _():
    k = Clock(); c = C(2, 10, clock=k); c.put("old", 1); k.t += 5; c.put("fresh", 2)
    k.t += 6   # old abgelaufen, fresh gültig
    c.put("new", 3)
    assert c.get("fresh") == 2 and c.get("new") == 3
@check("len zählt nur gültige Einträge")
def _():
    k = Clock(); c = C(3, 10, clock=k); c.put("a", 1); c.put("b", 2); k.t += 10; c.put("c", 3)
    assert len(c) == 1, len(c)
@check("ungültige Parameter → ValueError")
def _():
    for args in [(0, 10), (2, 0), (2, -1)]:
        try: C(*args)
        except ValueError: continue
        raise AssertionError(f"kein ValueError für {args}")
done()
'''

SESS_PROMPT = """\
Schreibe mit pandas eine Funktion `sessionize(events: pd.DataFrame) -> pd.DataFrame`.

Eingabe: Spalten user_id (str), timestamp (ISO-String, z. B. "2026-03-01 08:15:00"), event (str).
Die Zeilen sind NICHT sortiert. Eine Session eines Nutzers endet, wenn zwischen zwei aufeinanderfolgenden
Events MEHR als 30 Minuten liegen (genau 30 Minuten gehören noch zur selben Session).

Ausgabe: eine Zeile pro Session mit den Spalten (genau in dieser Reihenfolge)
user_id, session_id (1, 2, 3 … je Nutzer chronologisch), start, end (pd.Timestamp),
dauer_min (float, Minuten zwischen erstem und letztem Event), events (int, Anzahl),
first_event (event-Name des ersten Events). Sortiert nach user_id, session_id, Index 0..n-1.
Leere Eingabe → leeres DataFrame mit genau diesen Spalten. Die Eingabe darf nicht verändert werden.
Vektorisiert lösen (keine Python-Schleife über Zeilen). Gib nur einen Python-Codeblock aus."""

SESS_TESTS = r'''
import pandas as pd
COLS = ["user_id", "session_id", "start", "end", "dauer_min", "events", "first_event"]
df = pd.DataFrame({
    "user_id": ["b", "a", "a", "a", "b", "a", "a", "c"],
    "timestamp": ["2026-03-01 10:00:00", "2026-03-01 08:30:00", "2026-03-01 08:00:00",
                  "2026-03-01 09:00:00", "2026-03-01 10:30:00", "2026-03-01 09:30:01",
                  "2026-03-02 08:00:00", "2026-03-01 12:00:00"],
    "event": ["login", "klick", "login", "kauf", "logout", "klick", "login", "login"],
})
snap = df.copy()
STATE = {}
@check("läuft fehlerfrei, richtige Spalten")
def _():
    r = solution.sessionize(df); STATE["r"] = r
    assert list(r.columns) == COLS, list(r.columns)
@check("Session-Grenzen (genau 30 min = gleiche Session, 30 min 1 s = neu)")
def _():
    r = STATE["r"]
    got = list(zip(r.user_id, r.session_id, r.events))
    assert got == [("a", 1, 3), ("a", 2, 1), ("a", 3, 1), ("b", 1, 2), ("c", 1, 1)], got
@check("start/end/dauer_min korrekt")
def _():
    r = STATE["r"]
    assert r.start.iloc[0] == pd.Timestamp("2026-03-01 08:00:00")
    assert r.end.iloc[0] == pd.Timestamp("2026-03-01 09:00:00")
    assert abs(r.dauer_min.iloc[0] - 60.0) < 1e-9 and r.dauer_min.iloc[1] == 0.0
    assert abs(r.dauer_min.iloc[3] - 30.0) < 1e-9
@check("first_event chronologisch korrekt")
def _():
    assert list(STATE["r"].first_event) == ["login", "klick", "login", "login", "login"]
@check("Typen: session_id/events int, start Timestamp, Index 0..n-1")
def _():
    r = STATE["r"]
    assert pd.api.types.is_integer_dtype(r.session_id) and pd.api.types.is_integer_dtype(r.events)
    assert pd.api.types.is_datetime64_any_dtype(r.start) and list(r.index) == list(range(len(r)))
@check("Eingabe unverändert")
def _(): pd.testing.assert_frame_equal(df, snap)
@check("leere Eingabe → leeres DataFrame mit Spalten")
def _():
    r = solution.sessionize(df.iloc[0:0])
    assert len(r) == 0 and list(r.columns) == COLS
@check("vektorisiert (keine iterrows/itertuples/apply über Zeilen)")
def _():
    assert not re.search(r"iterrows|itertuples|for\s+\w+\s*,\s*\w+\s+in\s+\w+\.iterrows", SRC)
@check("skaliert: 200k Events < 5 s")
def _():
    import numpy as np, time
    rng = np.random.default_rng(0)
    n = 200_000
    big = pd.DataFrame({"user_id": rng.integers(0, 2000, n).astype(str),
                        "timestamp": (pd.Timestamp("2026-01-01") + pd.to_timedelta(rng.integers(0, 30*24*3600, n), unit="s")).astype(str),
                        "event": rng.choice(["a", "b"], n)})
    t = time.time(); solution.sessionize(big); el = time.time() - t
    METRICS["big_s"] = round(el, 2); assert el < 5, el
done()
'''

SQL_PROMPT = """\
SQLite-Schema:
  customers(id INTEGER PRIMARY KEY, name TEXT, country TEXT)
  orders(id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT 'YYYY-MM-DD', status TEXT)
  order_items(order_id INTEGER, product TEXT, qty INTEGER, unit_price REAL)

Schreibe EINE SQL-Abfrage: Für jeden Monat des Jahres 2026 die zwei umsatzstärksten Kunden.
Umsatz = Summe qty * unit_price, nur Bestellungen mit status = 'paid'. Bei Gleichstand entscheidet der
Kundenname aufsteigend. Monate mit nur einem zahlenden Kunden liefern nur eine Zeile.
Spalten genau: month ('YYYY-MM'), rank (1 oder 2), customer_name, revenue (auf 2 Nachkommastellen gerundet).
Sortiert nach month, rank. Gib nur die Abfrage in einem ```sql-Codeblock aus."""

SQL_TESTS = r'''
import sqlite3, random
from collections import defaultdict
db = sqlite3.connect(":memory:")
db.executescript("""
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, country TEXT);
CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT, status TEXT);
CREATE TABLE order_items(order_id INTEGER, product TEXT, qty INTEGER, unit_price REAL);
""")
rnd = random.Random(7)
names = ["Anna", "Bernd", "Cem", "Dana", "Emil", "Fatma", "Gero"]
for i, n in enumerate(names, 1): db.execute("INSERT INTO customers VALUES (?,?,?)", (i, n, "DE"))
oid = 0
for month in range(1, 13):
    for _ in range(12):
        oid += 1
        cust = rnd.randint(1, 7)
        status = rnd.choice(["paid", "paid", "paid", "cancelled", "pending"])
        db.execute("INSERT INTO orders VALUES (?,?,?,?)", (oid, cust, f"2026-{month:02d}-{rnd.randint(1, 28):02d}", status))
        for _ in range(rnd.randint(1, 3)):
            db.execute("INSERT INTO order_items VALUES (?,?,?,?)", (oid, "p", rnd.randint(1, 5), round(rnd.uniform(5, 80), 2)))
# Sonderfälle: Gleichstand im Mai, Monat mit einem Kunden (Dezember 2025 darf nicht zählen), Juni nur 1 Kunde
db.execute("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE order_date LIKE '2026-05%' OR order_date LIKE '2026-06%')")
db.execute("DELETE FROM orders WHERE order_date LIKE '2026-05%' OR order_date LIKE '2026-06%'")
for (o, c, d, s, q, p) in [(900, 5, "2026-05-03", "paid", 2, 50.0), (901, 2, "2026-05-09", "paid", 1, 100.0),
                           (902, 3, "2026-05-10", "paid", 1, 99.99), (903, 4, "2026-06-01", "paid", 3, 10.0),
                           (904, 1, "2026-06-02", "cancelled", 9, 99.0), (905, 6, "2025-12-30", "paid", 99, 99.0)]:
    db.execute("INSERT INTO orders VALUES (?,?,?,?)", (o, c, d, s))
    db.execute("INSERT INTO order_items VALUES (?,?,?,?)", (o, "p", q, p))
# Erwartung in Python berechnen
rev = defaultdict(float)
for d, s, name, q, p in db.execute("SELECT o.order_date, o.status, c.name, i.qty, i.unit_price FROM orders o "
                                    "JOIN customers c ON c.id=o.customer_id JOIN order_items i ON i.order_id=o.id"):
    if s == "paid" and d.startswith("2026"):
        rev[(d[:7], name)] += q * p
by_month = defaultdict(list)
for (m, n), r in rev.items(): by_month[m].append((round(r, 2), n))
expected = []
for m in sorted(by_month):
    for rank, (r, n) in enumerate(sorted(by_month[m], key=lambda x: (-x[0], x[1]))[:2], 1):
        expected.append((m, rank, n, r))
STATE = {}
@check("Abfrage läuft fehlerfrei")
def _():
    cur = db.execute(SRC.strip().rstrip(";"))
    STATE["cols"] = [d[0] for d in cur.description]; STATE["rows"] = cur.fetchall()
@check("Spaltennamen exakt")
def _(): assert STATE["cols"] == ["month", "rank", "customer_name", "revenue"], STATE["cols"]
@check("richtige Anzahl Zeilen (Juni nur 1, 2025 ignoriert)")
def _(): assert len(STATE["rows"]) == len(expected), (len(STATE["rows"]), len(expected))
@check("Gleichstand per Name aufgelöst (Mai: Bernd vor Emil)")
def _():
    may = [r for r in STATE["rows"] if r[0] == "2026-05"]
    assert [r[2] for r in may] == ["Bernd", "Emil"], may
@check("alle Zeilen + Umsätze korrekt")
def _():
    got = [(r[0], int(r[1]), r[2], round(float(r[3]), 2)) for r in STATE["rows"]]
    assert got == expected, [g for g, e in zip(got, expected) if g != e][:3]
@check("stornierte/offene Bestellungen ausgeschlossen")
def _():
    jun = [r for r in STATE["rows"] if r[0] == "2026-06"]
    assert [r[2] for r in jun] == ["Dana"], jun
done()
'''

ASYNC_PROMPT = """\
Schreibe in Python (asyncio, nur Standardbibliothek):

    async def fetch_all(urls: list[str], fetch, *, max_concurrency: int = 5, retries: int = 2,
                        timeout: float = 1.0, backoff_base: float = 0.01) -> dict[str, object]

- `fetch` ist eine async-Funktion `await fetch(url)`, die ein Ergebnis liefert oder eine Exception wirft.
- Höchstens `max_concurrency` fetch-Aufrufe gleichzeitig aktiv.
- Jeder einzelne Versuch hat ein Zeitlimit von `timeout` Sekunden.
- Bei Exception oder Timeout bis zu `retries` Wiederholungen; vor Wiederholung n (n = 0, 1, …) wird
  backoff_base * 2**n Sekunden gewartet. Während des Wartens darf der Slot nicht blockiert werden.
- Ergebnis: dict url → Ergebnis; schlägt der letzte Versuch fehl, ist der Wert die letzte Exception (als Objekt,
  nicht geworfen). Die Reihenfolge der Schlüssel entspricht `urls`.
- asyncio.CancelledError darf NICHT als Fehler geschluckt werden.
Gib nur einen Python-Codeblock aus."""

ASYNC_TESTS = r'''
import asyncio, time
F = solution.fetch_all
def run(c): return asyncio.run(c)

@check("Ergebnisse korrekt, Reihenfolge wie Eingabe")
def _():
    async def f(u): await asyncio.sleep(0.01 * (5 - int(u))); return u * 2
    r = run(F(["1", "2", "3", "4"], f))
    assert list(r.items()) == [("1", "11"), ("2", "22"), ("3", "33"), ("4", "44")], r
@check("max_concurrency eingehalten und ausgenutzt")
def _():
    st = {"cur": 0, "peak": 0}
    async def f(u):
        st["cur"] += 1; st["peak"] = max(st["peak"], st["cur"])
        await asyncio.sleep(0.03); st["cur"] -= 1; return u
    run(F([str(i) for i in range(20)], f, max_concurrency=4))
    assert st["peak"] == 4, st
@check("Retry nach Fehlern, dann Erfolg")
def _():
    n = {"c": 0}
    async def f(u):
        n["c"] += 1
        if n["c"] < 3: raise ConnectionError("x")
        return "ok"
    assert run(F(["u"], f, retries=2)) == {"u": "ok"} and n["c"] == 3
@check("endgültiger Fehler → Exception-Objekt, retries+1 Versuche")
def _():
    n = {"c": 0}
    async def f(u): n["c"] += 1; raise ValueError("kaputt")
    r = run(F(["u"], f, retries=3))
    assert isinstance(r["u"], ValueError) and n["c"] == 4, (r, n)
@check("Timeout pro Versuch")
def _():
    async def f(u): await asyncio.sleep(2); return u
    t = time.time(); r = run(F(["u"], f, retries=1, timeout=0.05)); el = time.time() - t
    assert isinstance(r["u"], (asyncio.TimeoutError, TimeoutError)) and el < 0.6, (r, el)
@check("exponentieller Backoff (≈0.05, 0.10 s)")
def _():
    ts = []
    async def f(u): ts.append(time.monotonic()); raise OSError()
    run(F(["u"], f, retries=2, backoff_base=0.05))
    g1, g2 = ts[1] - ts[0], ts[2] - ts[1]
    assert 0.045 <= g1 < 0.09 and 0.095 <= g2 < 0.17, (g1, g2)
@check("Backoff blockiert keinen Slot")
def _():
    calls = []
    async def f(u):
        calls.append((u, time.monotonic()))
        if u == "bad": raise OSError()
        await asyncio.sleep(0.01); return u
    t0 = time.monotonic()
    run(F(["bad", "g1", "g2", "g3"], f, max_concurrency=1, retries=1, backoff_base=0.3))
    g_end = max(t for u, t in calls if u == "g3") - t0
    assert g_end < 0.2, g_end
@check("leere Liste → {}")
def _(): assert run(F([], None)) == {}
@check("CancelledError wird nicht geschluckt")
def _():
    async def f(u): await asyncio.sleep(5)
    async def main():
        task = asyncio.create_task(F(["a", "b"], f, timeout=10))
        await asyncio.sleep(0.05); task.cancel()
        try: await task
        except asyncio.CancelledError: return "cancelled"
        return "geschluckt"
    assert run(main()) == "cancelled"
done()
'''

TASKS = [
    dict(id="random_forest", title="Random-Forest-Churn-Modell (sklearn)", prompt=RF_PROMPT, tests=RF_TESTS, lang="python"),
    dict(id="expr_parser", title="Ausdrucks-Parser ohne eval", prompt=EXPR_PROMPT, tests=EXPR_TESTS, lang="python"),
    dict(id="bugfix_ttl_cache", title="Bugfix: LRU-Cache mit TTL", prompt=CACHE_PROMPT, tests=CACHE_TESTS, lang="python"),
    dict(id="pandas_sessions", title="pandas: Sessionisierung", prompt=SESS_PROMPT, tests=SESS_TESTS, lang="python"),
    dict(id="sql_top_customers", title="SQL: Top-2-Kunden je Monat", prompt=SQL_PROMPT, tests=SQL_TESTS, lang="sql"),
    dict(id="async_fetch", title="asyncio: Fetch mit Limit/Retry/Timeout", prompt=ASYNC_PROMPT, tests=ASYNC_TESTS, lang="python"),
]


# ====================================================================== Ausführung

def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def extract(text: str, lang: str) -> str:
    text = strip_think(text)
    if lang == "sql":
        blocks = re.findall(r"```sql\s*\n(.*?)```", text, re.S | re.I) or re.findall(r"```\w*\s*\n(.*?)```", text, re.S)
    else:
        blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", text, re.S | re.I) or re.findall(r"```\s*\n(.*?)```", text, re.S)
    return max(blocks, key=len) if blocks else text


def run_tests(task: dict, code: str) -> dict:
    fname = "solution.sql" if task["lang"] == "sql" else "solution.py"
    with tempfile.TemporaryDirectory() as d:
        Path(d, fname).write_text(code, encoding="utf-8")
        prefix = HARNESS.replace("SOLUTION_FILE", repr(fname))
        if task["lang"] == "python":
            prefix += ("try:\n    import solution\nexcept BaseException as e:\n"
                       "    print('##RESULT##' + json.dumps({'import_error': (type(e).__name__ + ': ' + str(e))[:300]}))\n"
                       "    sys.exit(0)\n")
        Path(d, "hidden_test.py").write_text(prefix + task["tests"], encoding="utf-8")
        try:
            r = subprocess.run([PY, "hidden_test.py"], cwd=d, capture_output=True, text=True,
                               timeout=TEST_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return {"error": f"Tests > {TEST_TIMEOUT_S} s (Endlosschleife?)", "results": []}
    m = re.search(r"##RESULT##(.*)", r.stdout)
    if not m:
        return {"error": "Testlauf abgestürzt: " + (r.stderr.strip().splitlines() or ["?"])[-1][:300], "results": []}
    return json.loads(m.group(1))


def chat(model: str, prompt: str, think) -> tuple[str, dict]:
    payload = {"model": model, "messages": messages_with_persona([{"role": "user", "content": prompt}]),
               "stream": False, "keep_alive": "15m",
               "options": {"num_ctx": NUM_CTX, "num_predict": NUM_PREDICT, "temperature": 0.2}}
    if think is not None:
        payload["think"] = think
    t0 = time.time()
    r = requests.post(HOST + "/api/chat", json=payload, timeout=GEN_TIMEOUT_S)
    if r.status_code == 400 and "think" in r.text and think is not None:
        return chat(model, prompt, None)
    r.raise_for_status()
    data = r.json()
    msg = data.get("message", {})
    return msg.get("content", ""), {
        "wall_s": round(time.time() - t0, 1), "gen_tok": data.get("eval_count", 0),
        "tok_s": round(data.get("eval_count", 0) / (data.get("eval_duration", 1) / 1e9), 1) if data.get("eval_duration") else 0,
        "thinking_chars": len(msg.get("thinking") or ""), "done_reason": data.get("done_reason")}


def score(tr: dict) -> float:
    res = tr.get("results") or []
    if not res:
        return 0.0
    return round(sum(1 for _, ok, _ in res if ok) / len(res), 3)


def unload(model: str) -> None:
    try:
        requests.post(HOST + "/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
    except requests.RequestException:
        pass


def check_reference(ref_dir: Path, tasks: list[dict]) -> None:
    for t in tasks:
        ext = "sql" if t["lang"] == "sql" else "py"
        tr = run_tests(t, Path(ref_dir, f"{t['id']}.{ext}").read_text())
        print(f"{t['id']:<20} {score(tr):.2f}  {tr.get('metrics', '')}  {tr.get('error', tr.get('import_error', ''))}")
        for name, ok, err in tr.get("results", []):
            if not ok:
                print(f"    ✗ {name}: {err}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--think", choices=["off", "on"], default="off")
    ap.add_argument("--only")
    ap.add_argument("--out", default=str(ROOT / "code_bench_results.json"))
    ap.add_argument("--check-reference", type=Path)
    a = ap.parse_args()
    tasks = [t for t in TASKS if not a.only or t["id"] in a.only.split(",")]
    if a.check_reference:
        return check_reference(a.check_reference, tasks)
    think = a.think == "on"
    out = Path(a.out)
    results = json.loads(out.read_text()) if out.exists() else {}
    for m in requests.get(HOST + "/api/ps", timeout=10).json().get("models", []):
        unload(m["name"])
    for model in a.models:
        key = f"{model} | think={a.think}"
        print(f"\n=== {key} ===", flush=True)
        rows = []
        for t in tasks:
            try:
                answer, stats = chat(model, t["prompt"], think)
                code = extract(answer, t["lang"])
                tr = run_tests(t, code)
            except Exception as exc:  # noqa: BLE001
                answer, stats, code, tr = "", {}, "", {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            row = {"id": t["id"], "score": score(tr), "stats": stats, "tests": tr.get("results", []),
                   "metrics": tr.get("metrics", {}), "error": tr.get("error") or tr.get("import_error"),
                   "answer": answer, "code": code}
            rows.append(row)
            print(f"  {t['id']:<20} {row['score']:.2f}  {stats.get('wall_s', '-')} s  {stats.get('gen_tok', '-')} tok"
                  f"  {row['metrics'] or ''} {row['error'] or ''}", flush=True)
            results[key] = {"tasks": rows}
            out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
        unload(model)
    print("\n=== Übersicht ===")
    for key, d in results.items():
        ts = d["tasks"]
        print(f"  {key:<45} {100 * sum(t['score'] for t in ts) / len(ts):5.1f} %  "
              + "  ".join(f"{t['id'][:8]}={t['score']:.2f}" for t in ts))
    print(f"\nRohdaten: {out}")


if __name__ == "__main__":
    main()
