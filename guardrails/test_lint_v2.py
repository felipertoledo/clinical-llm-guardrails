#!/usr/bin/env python3
"""Linter regression suite — attack cases derived from the rules themselves
(4-D: no doubt markers; 4-E: legal documents; 8: patient handouts).
Run: python test_lint_v2.py  (or pytest)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
LINT = HERE / "lint_4d.py"
FX = HERE / "fixtures"


def run(texto, *args, suffix=".md"):
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
    f.write(texto); f.close()
    r = subprocess.run([sys.executable, str(LINT), f.name, *args], capture_output=True, text=True)
    os.unlink(f.name)
    return r.returncode, r.stdout


def confere(casos):
    falhas = []
    for rotulo, texto, args, esperado in casos:
        code, out = run(texto, *args)
        if code != esperado:
            falhas.append(f"{rotulo}: esperado {esperado}, veio {code}\n{out}")
    assert not falhas, "\n".join(falhas)


def test_arquivo_vazio_nao_e_limpo():
    code, out = run("")
    assert code == 2 and "ILEGÍVEL" in out, out


def test_pdf_sem_texto_nao_e_limpo():
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    path = tempfile.mktemp(suffix=".pdf")
    c = canvas.Canvas(path, pagesize=A4); c.rect(60, 500, 470, 280); c.showPage(); c.save()
    r = subprocess.run([sys.executable, str(LINT), path], capture_output=True, text=True)
    os.unlink(path)
    assert r.returncode == 2 and "ILEGÍVEL" in r.stdout, r.stdout


def test_campos_e_datas_em_branco_bloqueiam():
    confere([
        ("Data", "Data: ___/___/______\n", [], 1),
        ("Destinatário", "Destinatário: ____________\n", [], 1),
        ("CID", "CID: ________\n", [], 1),
        ("Finalidade", "Finalidade: ___________\n", [], 1),
        ("Período", "Período de afastamento: ______ dias\n", [], 1),
        ("Telefone", "Telefone: ___________\n", [], 1),
        ("data com 2 sublinhados", "Mogi Guaçu, __/__/____\n", [], 1),
        ("campo qualquer em 4-E", "Observação do perito: ______\n", ["--perfil", "legal"], 1),
        ("campo de diário (controle)", "Aconteceu: ____ Aprendi: ____________\n", [], 0),
        ("data da assinatura do paciente (controle)", "Assinatura — data: ___/___/______\n", ["--perfil", "legal"], 0),
    ])


def test_honorifico_e_titulo():
    confere([
        ("caixa alta", "DR. FELIPE RIBEIRO TOLEDO — MÉDICO — CRM-SP 216.986\n", [], 1),
        ("doutor", "Assinado: Doutor Felipe Ribeiro Toledo\n", [], 1),
        ("minúsculo sem ponto", "dr felipe ribeiro toledo\n", [], 1),
        ("médico de família", "Felipe Ribeiro Toledo — Médico de Família e Comunidade — CRM-SP 216.986\n", [], 1),
        ("MFC", "Felipe Ribeiro Toledo — MFC — CRM-SP 216.986\n", [], 1),
        ("colega (controle)", "Ao colega: Dr. Carlos — médico de família da UBS de referência.\n", [], 0),
    ])


def test_placeholders_e_marcas():
    confere([
        ("(a combinar)", "Retorno: (a combinar)\n", [], 1),
        ("não informado", "Endereço: não informado\n", [], 1),
        ("xx/xx/xxxx", "Data da consulta: xx/xx/xxxx\n", [], 1),
        ("???", "Dose de losartana ???\n", [], 1),
        ("a ser definida", "Retorno em data a ser definida.\n", [], 1),
        ("a ser confirmado", "Diagnóstico a ser confirmado.\n", [], 1),
        ("(checar)", "Alergia a dipirona (checar)\n", [], 1),
        ("(revisar)", "Dose de insulina (revisar)\n", [], 1),
        ("Fulano", "Paciente: Fulano de Tal\n", [], 1),
        ("00/00/0000", "Data de nascimento: 00/00/0000\n", [], 1),
        ("XX anos", "Paciente de XX anos\n", [], 1),
        ("{nome}", "Prezada {nome}\n", [], 1),
        ("<nome>", "Paciente: <nome do paciente>\n", [], 1),
        ("marca quebrada", "Diagnóstico: F03 (a\nconfirmar)\n", [], 1),
        ("⚠ com seletor", "Levodopa ⚠️ conferir apresentação\n", [], 1),
        ("tag de suporte", "Iniciar sertralina 50 mg conforme diretriz {CE}\n", [], 1),
        ("resultado pendente é informação (controle)", "Resultado da biópsia: pendente\n", [], 0),
        ("prosa (controle)", "Dor há cerca de 3 meses; faremos a verificação da pressão no retorno.\n", [], 0),
    ])


def test_colchetes_do_esqueleto_bloqueiam_e_proveniencia_passa():
    confere([
        ("[especialidade]", "ENCAMINHAMENTO — [especialidade]\n", [], 1),
        ("[paciente]", "Segundo [paciente], a dor começou há 3 meses.\n", [], 1),
        ("[mãe/pai/…]", "Renata, na condição de [mãe/pai/responsável legal/curador] de Larissa\n", [], 1),
        ("[exame, data]", "conforme laudo de [exame, data]\n", [], 1),
        ("[CPF]", "CPF: [CPF]\n", [], 1),
        ("[texto]", "Pergunta ao especialista: [texto]\n", [], 1),
        ("[Emitir via Memed]", "Losartana 50 mg [Emitir via Memed]\n", [], 1),
        ("colchete sem par", "consulta [presencial / por\n", [], 1),
        ("caixas de marcar (controle)", "Tomou o remédio? [ ] sim [ ] não [x] esqueci\n", ["--perfil", "paciente"], 0),
    ])
    r = subprocess.run([sys.executable, str(LINT), str(FX / "provenance_markers.md")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout   # provenance markers are not placeholders


def test_real_false_positives_pass():
    """Sentences taken from real documents that LOOK like doubt markers but are
    not (verbs in prose, diary lines, a colleague's "Dr."). They must pass."""
    r = subprocess.run([sys.executable, str(LINT), str(FX / "real_false_positives.md")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert "campo_leitor" in r.stdout   # diary blanks become a warning, not a block


def test_perguntar_por_perfil():
    confere([
        ("a esclarecer", "Etiologia a esclarecer.\n", [], 3),
        ("salvo engano", "Em uso de losartana 50 mg, salvo engano.\n", [], 3),
        ("talvez em documento técnico", "Possivelmente trata-se de enxaqueca; talvez seja preciso exame.\n", [], 3),
        ("talvez em material educativo (controle)", "Talvez você sinta sono nos primeiros dias.\n", ["--perfil", "paciente"], 0),
        ("não esclarecido é proveniência (controle)", "AVC: não esclarecido — relato contraditório da esposa.\n", [], 0),
    ])


def test_material_educativo_regra_8():
    confere([
        ("CID", "O que você tem: depressão (CID F32)\n", ["--perfil", "paciente"], 1),
        ("declaro", "Declaro que o paciente está em tratamento.\n", ["--perfil", "paciente"], 1),
        ("atesto", "Atesto para os devidos fins.\n", ["--perfil", "paciente"], 1),
        ("é portador de", "O paciente é portador de diabetes.\n", ["--perfil", "paciente"], 1),
        ("número de norma", "Conforme a Resolução CFM nº 2.314/2022.\n", ["--perfil", "paciente"], 1),
    ])
    import lint_4d as L
    corpo = "Orientações\nTome o remédio de manhã, depois do café.\n" + L.ASSINATURA + "\n"
    assert run(corpo, "--perfil", "paciente", "--final")[0] == 1            # no footer
    completo = corpo + L.RODAPE_EDUCATIVO + "\n" + L.RODAPE_CFM + "\n"
    code, out = run(completo, "--perfil", "paciente", "--final")
    assert code == 0, out
    quebrado = corpo + L.RODAPE_EDUCATIVO.replace("feito para", "feito\npara") + "\n" + L.RODAPE_CFM.replace("(Res.", "\n(Res.") + "\n"
    assert run(quebrado, "--perfil", "paciente", "--final")[0] == 0         # PDF line break


def test_perfil_legal_cobra_as_pecas_da_4e():
    import lint_4d as L
    base = ("RELATÓRIO MÉDICO\nPaciente: Maria Teste | DN: 12/03/1961 | Documento: CPF 123.456.789-09\n"
            "1. RELATO DA PACIENTE\nRefere dor lombar há 2 anos.\n2. AO EXAME, EM 04/11/2026\nNão realizado exame físico nesta consulta.\n"
            "Mogi Guaçu-SP, 04/11/2026.\n")
    bloco = (L.CONCORDANCIA + " 04/11/2026.\n________________________________________\nMaria Teste\n"
             + L.CONCORDANCIA_ASSINATURA + " ___/___/______\n")
    fim = L.ASSINATURA + "\n" + L.RODAPE_CFM + "\n"
    code, out = run(base + bloco + fim, "--perfil", "legal", "--final")
    assert code == 0, out
    code, out = run(base + fim, "--perfil", "legal", "--final")
    assert code == 1 and "falta_concordancia" in out, out
    code, out = run(base + bloco.replace("corresponde ao que informei", "confere com o que relatei") + fim, "--perfil", "legal", "--final")
    assert code == 1 and "falta_concordancia" in out, out                    # wording not exact
    code, out = run(base + bloco + L.ASSINATURA + "\n", "--perfil", "legal", "--final")
    assert code == 1 and "falta_rodape_cfm" in out, out
    sem_cpf = base.replace(" | Documento: CPF 123.456.789-09", "")
    code, out = run(sem_cpf + bloco + fim, "--perfil", "legal", "--final")
    assert code == 1 and "falta_cpf" in out, out
    code, out = run(sem_cpf + bloco + fim, "--perfil", "legal", "--final", "--dispensa", "cpf")
    assert code == 0 and "dispensado por decisão do Felipe" in out, out


def test_calibracao_no_acervo_real_11nov():
    """False blocks found when calibrating on real documents — must not come back."""
    confere([
        ("'Fulana' como exemplo em prosa", "Mandar uma mensagem para a Fulana já é uma ação.\n", ["--perfil", "paciente"], 0),
        ("'Fulano' em campo continua bloqueando", "Paciente: Fulano\n", [], 1),
        ("adendo datado em duas linhas", "prévia, já que não há uso crônico. [Adendo 14/10: a ressalva anterior sobre\ninibição enzimática deixa de se aplicar — fármaco suspenso.]\n", [], 0),
        ("placeholder em duas linhas continua bloqueando", "consulta [presencial / por\ntelemedicina] em 04/11/2026\n", [], 1),
    ])
    code, out = run("DN: ____/____/______ · CNS: __________________\n")
    assert code == 1 and "campo_identificador" in out and "data_branco" not in out, out   # same line must not be reported twice
    import lint_4d as L
    sem_travessao = "Encaminhamento\nFelipe Ribeiro Toledo Médico CRM-SP 216.986\n"
    assert run(sem_travessao, "--final")[0] == 0                             # header drawn in separate pieces
    assert run("Encaminhamento\nMédico assistente: Felipe Ribeiro Toledo\n", "--final")[0] == 1


def test_documento_escolar_nao_leva_cpf():
    import lint_4d as L
    fim = (L.CONCORDANCIA + " 04/11/2026.\nRenata Teste, na condição de mãe de Larissa Teste\n"
           + L.CONCORDANCIA_ASSINATURA + " ___/___/______\n" + L.ASSINATURA + "\n" + L.RODAPE_CFM + "\n")
    escola = "RELATÓRIO MÉDICO — À DIREÇÃO DA EMEF DE TESTE\nAluno(a): Larissa Teste | DN: 02/05/2017 | Turma: 4º ano\nSegundo relato da mãe, dor abdominal recorrente.\nEstiva Gerbi-SP, 11/11/2026.\n"
    code, out = run(escola + fim, "--perfil", "legal", "--final")
    assert code == 0 and "documento escolar" in out, out
    inss = "RELATÓRIO MÉDICO\nPaciente: Maria Teste | DN: 12/03/1961\nRefere dor lombar há 2 anos.\nEstiva Gerbi-SP, 11/11/2026.\n"
    code, out = run(inss + fim, "--perfil", "legal", "--final")
    assert code == 1 and "falta_cpf" in out, out
    # the CPF exemption applies to the school report, not to a request to the
    # Education Department (PNAE) without an "Aluno(a):" line
    pnae = ("SOLICITAÇÃO DE ADEQUAÇÃO ALIMENTAR (PNAE)\nÀ Secretaria Municipal de Educação\n"
            "Paciente: Larissa Teste | DN: 02/05/2017\nSegundo relato da mãe, não tolera leite.\n"
            "Estiva Gerbi-SP, 11/11/2026.\n")
    code, out = run(pnae + fim, "--perfil", "legal", "--final")
    assert code == 1 and "falta_cpf" in out, out


def test_token_manual_bloqueia_so_no_final():
    doc = "Convênio: Unimed — carteira: ______ «manual»\nFelipe Ribeiro Toledo — Médico — CRM-SP 216.986\n"
    assert run(doc)[0] == 0
    code, out = run(doc, "--final")
    assert code == 1 and "token_manual" in out, out


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    falhas = 0
    for t in tests:
        try:
            t(); print(f"ok   {t.__name__}")
        except AssertionError as e:
            falhas += 1; print(f"FAIL {t.__name__}\n{e}")
    print(f"\n{len(tests) - falhas}/{len(tests)} passaram")
    sys.exit(1 if falhas else 0)
