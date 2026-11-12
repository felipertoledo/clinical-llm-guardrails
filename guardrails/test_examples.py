#!/usr/bin/env python3
"""The README demo as a test: the faithful summary is released, the
hallucinated one is held. Run: pytest -q guardrails"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
EX = HERE.parent / "examples" / "teleconsult"


def trace(doc):
    r = subprocess.run([sys.executable, str(HERE / "rastreia_campos.py"), "--json",
                        "--fonte", str(EX / "source.txt"), "--transcricao", str(EX / "transcript.md"),
                        str(EX / doc)], capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout)[0]


def lint(doc):
    r = subprocess.run([sys.executable, str(HERE / "lint_4d.py"), str(EX / doc), "--final"],
                       capture_output=True, text=True)
    return r.returncode, r.stdout


def test_faithful_summary_is_released():
    code, j = trace("doc_faithful.md")
    assert code == 0 and not j["bloqueios"], j
    assert lint("doc_faithful.md")[0] == 0


def test_hallucinated_summary_is_held():
    code, j = trace("doc_hallucinated.md")
    assert code == 1
    found = {(b["tipo"], b["valor"]) for b in j["bloqueios"]}
    # vital signs "measured" in a teleconsult, a flipped age, an ICD code nobody
    # gave, a treatment duration that was never prescribed
    assert {("numero", "120/80"), ("dose", "98%"), ("idade", "43 anos"),
            ("cid", "J06.9"), ("posologia", "por 7 dias")} <= found, found
    code, out = lint("doc_hallucinated.md")
    assert code == 1 and "[confirmar]" in out, out      # "(a confirmar)" left on paper


def test_declared_limit_is_visible_in_the_demo():
    """"há 10 dias" slips through: 10 exists in the source (as "10 mg"). The
    tracer proves a number EXISTS in the source, not what it belongs to."""
    _, j = trace("doc_hallucinated.md")
    assert not any(b["tipo"] == "tempo" for b in j["bloqueios"]), j
