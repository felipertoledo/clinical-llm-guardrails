#!/usr/bin/env python3
"""Tracer regression suite — tampered data must block; a faithful document
must pass. Run: python test_rastreia_v2.py  (or pytest)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
R = HERE / "rastreia_campos.py"


def run(fonte, doc, suffix_fonte=".txt"):
    fs = tempfile.NamedTemporaryFile("w", suffix=suffix_fonte, delete=False, encoding="utf-8"); fs.write(fonte); fs.close()
    fd = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8"); fd.write(doc); fd.close()
    r = subprocess.run([sys.executable, str(R), "--json", "--fonte", fs.name, fd.name], capture_output=True, text=True)
    os.unlink(fs.name); os.unlink(fd.name)
    try:
        return r.returncode, json.loads(r.stdout)[0]
    except Exception:
        return r.returncode, {"bloqueios": [], "avisos": [], "saida": r.stdout}


def confere(casos):
    falhas = []
    for rotulo, fonte, doc, esperado in casos:
        code, j = run(fonte, doc)
        if code != esperado:
            falhas.append(f"{rotulo}: esperado {esperado}, veio {code} — {[(b['tipo'], b['valor']) for b in j.get('bloqueios', [])]}")
    assert not falhas, "\n".join(falhas)


def test_dose_trocada_bloqueia():
    confere([
        ("25 mg → 5 mg", "ubs: iniciei sertralina 25 mg de manhã.", "Sertralina 5 mg pela manhã.", 1),
        ("150 mg → 50 mg", "crt: venlafaxina 150 mg/dia.", "Venlafaxina 50 mg ao dia.", 1),
        ("0,25 → 0,5 mg", "clonazepam 0,25 mg à noite", "Clonazepam 0,5 mg à noite.", 1),
        ("dose sem unidade na fonte", "sertralina 50 pela manhã", "Sertralina 50 mg pela manhã.", 1),
        ("dose ausente", "sertralina pela manhã.", "Sertralina 75 mg pela manhã.", 1),
    ])


def test_numero_ano_e_data_inventados_bloqueiam():
    confere([
        ("ano com dígitos emendados da fonte", "ubs: seu João, glicemia 219, 58 kg, PA 130/80.", "Paciente: João — DN: 1958", 1),
        ("ano que só existe na lista canônica", "ubs: adolescente com cefaleia há 3 meses.", "Nascido em 2007, com cefaleia há 3 meses.", 1),
        ("data 21/2 → 1/2", "consulta em 21/2/2026, retorno combinado.", "Consulta realizada em 1/2/2026.", 1),
        ("número de 3 dígitos", "PA 130/80, sem queixas.", "Glicemia de jejum: 287.", 1),
    ])


def test_posologia_idade_e_tempo_bloqueiam():
    confere([
        ("8/8 h por 7 dias → 12/12 h por 10 dias", "amoxicilina 500 mg de 8 em 8 horas por 7 dias.",
         "Amoxicilina 500 mg de 12 em 12 horas por 10 dias.", 1),
        ("2x ao dia → 3x ao dia", "metformina 850 mg 2x ao dia", "Metformina 850 mg 3x ao dia.", 1),
        ("idade 72 → 27", "ubs: dona Maria, 72 anos, tontura há 2 semanas.", "Paciente de 27 anos com tontura há 2 semanas.", 1),
        ("há 3 meses → há 8 meses", "dor lombar há 3 meses.", "Refere dor lombar há 8 meses.", 1),
    ])


def test_numero_de_sigla_nao_vira_origem_de_prazo():
    """[v2.0.1] Found in an eval round: "GAD-7" in the source was accepted as the origin of "7 dias"."""
    fonte = "Paula Teste, 27a, teleconsulta. GAD-7 moderado, PHQ-9 baixo, vitamina B12 pedida. inicio sertralina 25mg."
    code, j = run(fonte, "Tomar sertralina 25 mg por 7 dias e depois me avisar.\n")
    assert code == 1 and any(b["tipo"] == "posologia" for b in j["bloqueios"]), j
    code, j = run(fonte, "Medir a pressão em até 7 dias. Retorno em 12 semanas.\n")
    assert {a["valor"] for a in j["avisos"]} >= {"7 dias", "12 semanas"}, j
    # the acronym itself may still appear in the document
    code, j = run(fonte, "Questionário GAD-7 com resultado moderado e PHQ-9 baixo. Sertralina 25 mg.\n")
    assert code == 0 and not j["bloqueios"], j


def test_documento_fiel_passa():
    confere([
        ("básico", "sertralina 50 mg pela manhã; retorno em 30 dias. nome: Maria Teste.",
         "Paciente: Maria Teste\nSertralina 50 mg pela manhã. Retorno em 30 dias.", 0),
        ("telegrama do Felipe", "amoxi 500mg 8/8h 7d, dona Maria 72a, tontura ha 2 sem",
         "Amoxicilina 500 mg de 8 em 8 horas por 7 dias. Paciente de 72 anos, com tontura há 2 semanas.", 0),
        ("número por extenso", "dor há três meses, toma duas vezes ao dia", "Refere dor há 3 meses. Toma 2 vezes ao dia.", 0),
        ("data por extenso × numérica", "laudo de RM de 5 de janeiro de 2021", "Conforme laudo de RM de 05/01/2021.", 0),
        ("data ISO no anexo", "exame coletado em 2026-09-14", "Exame de 14/09/2026.", 0),
        ("CPF com e sem pontuação", "cpf 12345678909", "Paciente: Maria Teste\nCPF: 123.456.789-09", 1),  # the name was not in the source
        ("CPF com e sem pontuação (nome dado)", "nome: Maria Teste, cpf 12345678909", "Paciente: Maria Teste\nCPF: 123.456.789-09", 0),
        ("SpO2 sem % na fonte", "SpO2 92 em repouso", "SpO2 92% em repouso.", 0),
        ("micrograma", "levotiroxina 50 mcg em jejum", "Levotiroxina 50 µg em jejum.", 0),
        ("pergunta 4-D confirmada", "Cidade do documento Mogi Guaçu-SP? Data de hoje, 11/11/2026? → sim",
         "Mogi Guaçu-SP, 11/11/2026", 0),
    ])


def test_orientacao_generica_vira_aviso_nao_bloqueio():
    code, j = run("febre desde ontem, sem sinais de alarme.",
                  "Procure a unidade se a febre durar mais de 3 dias ou passar de 39 °C. Beba 2 litros de água.")
    assert code == 0, j
    assert any(a["tipo"] == "medida" for a in j["avisos"]), j


def test_nome_solto_em_prosa_vai_para_o_verificador():
    code, j = run("relatório para a escola do menino, 9 anos.", "O aluno estuda na EMEF Professora Exemplo Teste.")
    assert code == 0 and any(a["tipo"] == "nome" for a in j["avisos"]), j


def test_limite_declarado_valor_nao_e_ligado_ao_item():
    """Declared limit: the program checks that the number exists in the source,
    not which drug it belongs to — that is the verifier's job."""
    code, _ = run("em uso: losartana 50 mg 12/12h; anlodipino 5 mg 1x/dia.", "Em uso de anlodipino 50 mg uma vez ao dia.")
    assert code == 0


def test_documento_ou_fonte_sem_texto_nao_e_rastreado():
    assert run("ubs: caso qualquer.", "")[0] == 2
    assert run("", "Paciente: Maria Teste")[0] == 2
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    pdf = tempfile.mktemp(suffix=".pdf")
    c = canvas.Canvas(pdf, pagesize=A4); c.rect(60, 500, 470, 280); c.showPage(); c.save()
    fs = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8"); fs.write("ubs: caso."); fs.close()
    r = subprocess.run([sys.executable, str(R), "--fonte", fs.name, pdf], capture_output=True, text=True)
    os.unlink(pdf); os.unlink(fs.name)
    assert r.returncode == 2 and "ILEGÍVEL" in r.stdout, r.stdout


def test_anexo_docx_entra_pelo_caminho():
    import zipfile
    docx = tempfile.mktemp(suffix=".docx")
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:body><w:p><w:r><w:t>Laudo: creatinina 1,4 mg/dL em 02/10/2026. "
                                        "Paciente Antônio Exemplo Teste.</w:t></w:r></w:p></w:body></w:document>")
    fd = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    fd.write("Paciente: Antônio Exemplo Teste\nConforme laudo de 02/10/2026."); fd.close()
    fs = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8"); fs.write("crt: encaminha o seu Antônio."); fs.close()
    r = subprocess.run([sys.executable, str(R), "--fonte", fs.name, "--fonte", docx, fd.name], capture_output=True, text=True)
    for f in (docx, fd.name, fs.name):
        os.unlink(f)
    assert r.returncode == 0, r.stdout


def test_transcricao_sustenta_dose_mas_nao_campo_de_identificacao():
    """Rule 4: a name heard during the visit does not become a document field."""
    d = tempfile.mkdtemp()
    Path(f"{d}/fonte.txt").write_text("crt: processa consulta. confirmo a tabela.", encoding="utf-8")
    Path(f"{d}/consulta.md").write_text("[00:03:10] Felipe: então, dona Helena Prado Teste, a sertralina fica em 50 mg de manhã.", encoding="utf-8")
    Path(f"{d}/ok.md").write_text("Sertralina 50 mg pela manhã.", encoding="utf-8")
    Path(f"{d}/campo.md").write_text("Paciente: Helena Prado Teste\nSertralina 50 mg pela manhã.", encoding="utf-8")
    def roda(doc):
        return subprocess.run([sys.executable, str(R), "--fonte", f"{d}/fonte.txt", "--transcricao", f"{d}/consulta.md", f"{d}/{doc}"],
                              capture_output=True, text=True)
    assert roda("ok.md").returncode == 0
    r = roda("campo.md")
    assert r.returncode == 1 and "[campo]" in r.stdout, r.stdout


def test_fonte_que_e_o_proprio_documento_gera_aviso():
    doc = "\n".join(f"Linha {i} do documento gerado, com texto suficiente para contar." for i in range(1, 8))
    code, j = run(doc, doc)
    assert any(a["tipo"] == "fonte" for a in j["avisos"]), j


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in tests:
        try:
            t(); print(f"ok   {t.__name__}")
        except AssertionError as e:
            falhas += 1; print(f"FAIL {t.__name__}\n{e}")
    print(f"\n{len(tests) - falhas}/{len(tests)} passaram")
    sys.exit(1 if falhas else 0)
