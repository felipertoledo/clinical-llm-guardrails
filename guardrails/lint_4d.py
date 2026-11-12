#!/usr/bin/env python3
"""
lint_4d.py — "flawless document" linter (rule 4-D) — v2.2

Blocks any document addressed to a third party (referral, report, legal
statement, patient handout) that still carries a doubt marker, an unfilled
placeholder or, with --final, a missing mandatory piece. Doubt is resolved
BEFORE the document exists (by asking the physician), never on paper.

Target language: Brazilian Portuguese clinical documents. The patterns and
the runtime messages are in Portuguese on purpose.

Usage:
    python lint_4d.py document.pdf --perfil legal --final
    python lint_4d.py document.md other.txt
    python lint_4d.py --json document.pdf        # JSON output
    cat text.txt | python lint_4d.py -            # stdin

Profiles:
    --perfil profissional (default) — referral, request, summary sent to
        another service: a blank identifier, date or addressee blocks.
    --perfil legal — document with legal weight (rule 4-E): everything in
        "profissional" + ANY labelled blank field blocks; with --final it
        requires the patient-agreement block (exact wording, visit date
        filled in), the "Assinatura — data:" line, the CFM 2.454 footer and
        the patient's CPF/CNS (a report addressed to a school is exempt from
        CPF; a request to the Education Department is not).
    --perfil paciente — patient education handout (rule 8): "Nome: ____" is
        a field the patient fills in (warning only); ICD codes, "declaro",
        "atesto", "é portador de" and regulation numbers block; with --final
        it requires the literal educational footer and the CFM 2.454 line.

--final  — this is the file that will be DELIVERED: a leftover «manual»
           token blocks and the mandatory pieces of the profile are required
           (canonical signature in all of them).
--dispensa cpf|concordancia — waiver, only by the physician's decision;
           recorded in the output.
--aceito-linha N — an ASK finding the physician decided to keep (repeatable).

Exit codes:
    0  clean — may be delivered
    1  blocked — findings listed
    2  UNREADABLE or usage error — the linter did NOT read the file (empty,
       PDF without a text layer, corrupted text). Never treat as green:
       review by hand and say the linter did not run.
    3  ASK — nothing blocks, but there is a point only the physician can
       decide ("provável", "salvo engano", "a esclarecer"; "talvez" outside
       patient handouts). STOP and ask. Kept by them → --aceito-linha N.

Declared exception (never inferred): a line carrying the «manual» token is
an INTENTIONALLY blank field (signature, card number filled in by hand) and
is skipped in the draft. The token must be REMOVED from the final document —
with --final it blocks.

Formats: .pdf (extracted text), .md, .txt, .html (text), or '-' (stdin).
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

VERSION = "2.2.0"
TOKEN_MANUAL = "«manual»"
TETO_PALAVRAS_PACIENTE = 700   # [v2.2] warning, not a block: page one has to do the job (rule 8)
PERFIS = ("profissional", "paciente", "legal")

# ---------------------------------------------------------------------------
# Canonical texts (rules 4, 4-E and 8). Single home: the PDF builder imports
# them from here, so the printed text and the required text are the same.
# ---------------------------------------------------------------------------
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from config_autor import (ASSINATURA, RX_ASSINATURA_COMPACTA, RX_LINHA_AUTOR,  # noqa: E402
                          RX_NOME_HONORIFICO)
RODAPE_EDUCATIVO = ("Este material é um resumo educativo da consulta, feito para você "
                    "entender e acompanhar seu tratamento. Ele não substitui atestado, "
                    "laudo, relatório ou prontuário e não vale como documento oficial.")
RODAPE_DIARIO = ("Este diário é uma ferramenta de apoio para a gente entender melhor o "
                 "que está acontecendo. Ele não substitui atestado, laudo, relatório ou "
                 "prontuário e não vale como documento oficial.")
RODAPE_CFM = ("Elaborado com apoio de ferramenta computacional sob revisão e "
              "responsabilidade técnica exclusiva do médico signatário "
              "(Res. CFM 2.454/2026).")
CONCORDANCIA = ("Declaro que li o relato acima e que ele corresponde ao que informei "
                "na consulta de")
CONCORDANCIA_ASSINATURA = "Assinatura — data:"

# ---------------------------------------------------------------------------
# Blocking patterns. Each entry: (id, regex, short description).
# v1.3 calibrated on 34 real documents: a verb in prose ("vamos confirmar")
# and a diary line are not markers; only the marker FORM blocks.
# v2.0: placeholders from the document skeletons, blank dates and fields,
# pending markers, leaked evidence-support tag.
# ---------------------------------------------------------------------------
ID_LABELS = (r"(?:paciente|nome|dn|data\s+de\s+nascimento|nascimento|cns|cpf|rg|"
             r"documento|endere[çc]o|cidade|munic[íi]pio|escola|s[ée]rie|turma|conv[êe]nio|"
             r"carteira|respons[áa]vel|idade|m[ãa]e|pai|"
             r"data|destinat[áa]rio|finalidade|cid(?:-10)?|"
             r"per[íi]odo(?:\s+de\s+afastamento)?|telefone|contato|especialidade|"
             r"prioridade|alun[oa]|institui[çc][ãa]o|empregador|empresa|cargo)")
PATTERNS = [
    ("confirmar", r"[\(\[]\s*(?:a\s+)?confirmar[^\)\]]*[\)\]]|⚠\s*(?:a\s+)?confirmar|"
                  r"\bconfirmar\s+(?:n[úu]mero|vig[êe]ncia|apresenta|dose|cid|data|nome|endere|cns|c[óo]digo)|"
                  r"[:—–]\s*a\s+confirmar\b|\ba\s+confirmar\s*$",
     "marca de confirmação pendente"),
    ("conferir", r"[\(\[]\s*conferir[^\)\]]*[\)\]]|⚠\s*conferir|"
                 r"\bconferir\s+(?:apresenta|vig[êe]ncia|n[úu]mero|dose|vigente|c[óo]digo|cid)",
     "marca 'conferir' (apresentação, número, vigência)"),
    ("verificar", r"\bverificar\b",
     "'verificar' — BLOQUEIA; se for instrução ao paciente ('verificar a pressão'), reescreva ('meça a pressão')"),
    ("alerta", r"⚠[^\n]{0,60}?\b(?:confirmar|conferir|verificar|checar|pendente|a\s+definir|d[úu]vida|incerto|revisar)\b",
     "⚠ junto de dúvida (⚠ de alerta clínico isolado passa)"),
    ("campo_identificador", r"\b" + ID_LABELS + r"\s*:?\s*_{3,}|"
                            r"[\wÀ-ú]+\s*[-—–]?\s*SP\s*,\s*_{2,}|_{3,}\s*[-—–]\s*SP\b",
     "identificador/local/data/destinatário em branco — perguntar antes (regra 4-D)"),
    ("data_branco", r"_{2,}\s*/\s*_{2,}\s*/\s*_{2,}",
     "data em branco — perguntar antes (só a data da ASSINATURA do paciente pode ficar em branco)"),
    ("concordancia_data", r"consulta\s+de\s*_{2,}",
     "data da consulta em branco no bloco de concordância (4-E exige preenchida)"),
    ("a_criterio", r"\ba\s+crit[eé]rio\b", "'a critério'"),
    # v1.6: "a definir" (TBD) only as a MARKER (field/end of sentence/brackets);
    # in prose ("ajuda a definir o melhor caminho") it is a verb and passes.
    ("a_definir", r"(?:[:(\[—–-]\s*|\b(?:data|hor[áa]rio|dose|retorno|prazo|local|cid|"
                  r"conduta|tratamento|especialidade|destino)\s+)a\s+definir\b|"
                  r"\ba\s+definir\s*(?:$|[)\].,;])", "'a definir'"),
    ("pendencia", r"[\(\[]\s*(?:a\s+combinar|checar|revisar|validar|ver(?:\s+depois)?|a\s+ver|"
                  r"sem\s+informa[çc][ãa]o|n/?i|s/?d)\s*[\)\]]|"
                  r"\ba\s+ser\s+(?:definid|confirmad|esclarecid|agendad|combinad|informad)[oa]s?\b|"
                  r"\b" + ID_LABELS + r"\s*:\s*(?:n[ãa]o\s+informad[oa]|n/i|s/d|sem\s+informa[çc][ãa]o|"
                  r"desconhecid[oa]|a\s+combinar)\b",
     "marca de pendência ('(checar)', 'a ser definido', campo 'não informado')"),
    ("tbd", r"\b(?:TBD|TODO|XXX)\b", "marcador de rascunho (TBD/TODO/XXX)"),
    ("interrogacao_campo", r"[\(\[\{]\s*\?\s*[\)\]\}]|:\s*\?\s*$|\?{2,}", "'?' solto em campo"),
    ("placeholder", r"\bDD/MM/AA(?:AA)?\b|\bxx/xx/xx(?:xx)?\b|\b00/00/0000\b|\bXX\s+anos\b|"
                    r"\{[a-zà-ú_ ]{2,30}\}|<(?!https?:|mailto:)[a-zà-ú][^<>\n]{1,40}>|"
                    r"\bfulan[oa]\s+de\s+tal\b|\b" + ID_LABELS + r"\s*:\s*fulan[oa]\b",
     "placeholder não preenchido (DD/MM/AAAA, xx/xx/xxxx, {nome}, <nome>, 'Fulano')"),
    ("tag_suporte", r"\{(?:D|RS|ECR|OBS|CE|RC)(?:[;\s⚠][^{}\n]*)?\}",
     "tag de suporte {D}/{CE}… vazou para documento a terceiro (regra 4-B)"),
]
CASE_SENSITIVE = {"tbd", "tag_suporte"}
# patterns also searched across a line break (marker split in two lines)
ENTRE_LINHAS = {"confirmar", "conferir", "pendencia", "a_definir"}

# A square bracket is a placeholder, unless it is a provenance marker (rule
# 4-C: [relato], [laudo], [observado]…), a checkbox, a numeric reference or a
# markdown link. v2.1: the pair is searched in the whole text (a dated
# addendum spans two lines).
COLCHETE = re.compile(r"\[([^\[\]]{0,600})\]")
COLCHETE_OK = re.compile(r"^\s*(?:relatos?|laudos?|observad[oa]s?|vist[oa]s?|aferid[oa]s?|"
                         r"examinad[oa]s?|referid[oa]s?|informad[oa]s?|sic|adendo)\b", re.IGNORECASE)
COLCHETE_CAIXA = re.compile(r"^\s*(?:[xX✓✔]?\s*|\d{1,3})$")

# ASK: the linter STOPS and asks, occurrence by occurrence. The physician
# decides to keep or rewrite — the linter never decides on its own.
# (id, regex, description, profiles where it applies — None = all)
ASK_PATTERNS = [
    ("provavel", r"\bprov[aá]vel(?:mente)?\b",
     "'provável' — PERGUNTAR ao Felipe: manter ou reescrever? (regra 4-D × regra 8)", None),
    ("salvo_engano", r"\bsalvo\s+engano\b",
     "'salvo engano' — PERGUNTAR ao Felipe: confirmar o dado ou reescrever", None),
    ("a_esclarecer", r"\ba\s+esclarecer\b",
     "'a esclarecer' — PERGUNTAR ao Felipe (≠ 'não esclarecido', que é proveniência)", None),
    ("hedge", r"\b(?:talvez|possivelmente|aparentemente|supostamente)\b",
     "hesitação em documento técnico — PERGUNTAR ao Felipe: manter ou reescrever?",
     ("profissional", "legal")),
]

# Rule 10: never "Dr." before the author's own name (a colleague's "Dr. X"
# is free). Title is only "Médico" on the name/licence line.
RX_HONORIFICO = re.compile(r"(?<![\wÀ-ú])(?:dr[aª]?|doutora?)\.?\s+"
                           + RX_NOME_HONORIFICO, re.IGNORECASE)
RX_LINHA_FELIPE = re.compile(RX_LINHA_AUTOR, re.IGNORECASE)
RX_TITULO = re.compile(r"m[ée]dico\s+de\s+fam[íi]lia|medicina\s+de\s+fam[íi]lia|\bMFC\b", re.IGNORECASE)

# Patient handout (rule 8): no ICD code, no "declaro"/"atesto", no medical
# assertion, no regulation number (only the CFM 2.454 footer line).
PACIENTE_PATTERNS = [
    ("cid_educativo", re.compile(r"\bCID(?:-?10)?\b"), "CID em material educativo (regra 8)"),
    ("declaro_atesto", re.compile(r"\b(?:declaro|atesto)\b", re.IGNORECASE),
     "'declaro'/'atesto' em material educativo — isso é documento 4-E, não material"),
    ("afirmacao_medica", re.compile(r"\b[ée]\s+portador(?:a)?\s+d[eao]s?\b|\bpaciente\s+portador(?:a)?\b",
                                    re.IGNORECASE),
     "afirmação médica ('é portador de') — escreva 'o que conversamos / o que você me contou'"),
    ("norma_educativo", re.compile(r"\b(?:Resolu[çc][ãa]o|Res\.|Portaria|RDC|Lei)\s+"
                                   r"(?:[A-Za-zÀ-ú/.\-]+\s+){0,3}(?:n[º°o.]?\s*)?\d"),
     "número de norma em material educativo (só a linha CFM 2.454 do rodapé)"),
]
RX_CAMPO_QUALQUER = re.compile(r"(?:^|[|•–—])\s*[A-Za-zÀ-ú][\wÀ-ú /().ºª\-]{1,40}:\s*_{2,}")
RX_ESCOLAR = re.compile(r"(?im)^\s*alun[oa](?:\(a\))?\s*:|\bà\s+dire[çc][ãa]o\s+d[ao]\b")
RX_CPF_CNS = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)|(?<!\d)\d{3}\s?\d{4}\s?\d{4}\s?\d{4}(?!\d)")

# WARNING (does not block): blank field that is not an identifier — diary,
# note, dose to be filled in by the patient. Counted for review.
WARN_PATTERNS = [
    ("campo_leitor", r"_{3,}", "campo para o leitor preencher — confirme que é intencional"),
]
SIGNATURE_LINE = re.compile(r"^\s*_{5,}\s*$")

COMPILED = [(i, re.compile(p, (0 if i in CASE_SENSITIVE else re.IGNORECASE) | re.UNICODE), d)
            for i, p, d in PATTERNS]
COMPILED_ASK = [(i, re.compile(p, re.IGNORECASE | re.UNICODE), d, perfis) for i, p, d, perfis in ASK_PATTERNS]
COMPILED_WARN = [(i, re.compile(p, re.IGNORECASE | re.UNICODE), d) for i, p, d in WARN_PATTERNS]


class Ilegivel(Exception):
    """The file has no text the linter can read."""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def read_text(path: str) -> str:
    if path == "-":
        text = sys.stdin.read()
    else:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(path)
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            text = _read_pdf(p)
        elif suffix in (".html", ".htm"):
            text = re.sub(r"<[^>]+>", " ", p.read_text(encoding="utf-8", errors="replace"))
        else:
            text = p.read_text(encoding="utf-8", errors="replace")
    checar_legivel(text, path)
    return text


def _read_pdf(p: Path) -> str:
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(p)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except ImportError:
        pass
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(p))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except ImportError as e:
        raise RuntimeError("instale pdfplumber ou pypdf para ler PDF") from e


def checar_legivel(text: str, path: str) -> None:
    """Fails CLOSED: without readable text there is no 'LIMPO' (clean)."""
    if not text.strip():
        raise Ilegivel("arquivo vazio")
    letras = sum(ch.isalpha() for ch in text)
    if str(path).lower().endswith(".pdf") and letras < 20:
        raise Ilegivel("PDF sem texto extraível (digitalizado ou só imagem)")
    ruins = text.count("�") + len(re.findall(r"\(cid:\d+\)", text))
    if ruins > max(5, letras * 0.2):
        raise Ilegivel("texto extraído corrompido (fonte sem mapa de caracteres)")


# ---------------------------------------------------------------------------
# Normalisation used to require mandatory pieces (exact text, tolerating line
# breaks, em dash × hyphen and accents lost in PDF extraction)
# ---------------------------------------------------------------------------
def _compacto(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("­", "")
    s = re.sub(r"[—–‐‑−]", "-", s)
    return re.sub(r"\s+", "", s)


def _pecas_obrigatorias(text: str, perfil: str, dispensas: set, notas: list) -> list:
    c = _compacto(text)
    faltas = []

    def falta(pid, desc):
        faltas.append({"id": pid, "line": 0, "match": "", "desc": desc, "context": "(peça obrigatória ausente)"})

    if not re.search(RX_ASSINATURA_COMPACTA, c):
        falta("falta_assinatura", f"assinatura canônica ausente: «{ASSINATURA}»")
    if perfil == "paciente":
        if _compacto(RODAPE_EDUCATIVO) not in c and _compacto(RODAPE_DIARIO) not in c:
            falta("falta_rodape_educativo", "rodapé educativo literal ausente (regra 8) — texto da orientação ou do diário")
        if _compacto(RODAPE_CFM) not in c:
            falta("falta_rodape_cfm", "linha CFM 2.454/2026 ausente no rodapé (regra 4)")
    if perfil == "legal":
        if "concordancia" not in dispensas:
            rx = re.escape(_compacto(CONCORDANCIA)) + r"(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}o?de[a-z]+de\d{4})"
            if not re.search(rx, c):
                falta("falta_concordancia", "bloco de concordância ausente, com texto diferente do exato, "
                                            "ou sem a data da consulta preenchida (regra 4-E)")
            if _compacto(CONCORDANCIA_ASSINATURA) not in c:
                falta("falta_concordancia_assinatura", "linha «Assinatura — data:» do paciente/responsável ausente (4-E)")
        if _compacto(RODAPE_CFM) not in c:
            falta("falta_rodape_cfm", "rodapé CFM 2.454/2026 ausente (regra 4-E)")
        escolar = RX_ESCOLAR.search(text)
        if escolar:
            notas.append("CPF não cobrado: documento escolar (decisão do Felipe, 11/11/2026)")
        if "cpf" not in dispensas and not escolar and not RX_CPF_CNS.search(text):
            falta("falta_cpf", "CPF/CNS do paciente ausente (regra 4-E; CFM 2.381/2024) — perguntar; "
                               "se o Felipe dispensar, rodar com --dispensa cpf")
    return faltas


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------
def _colchetes(text: str):
    """(line, excerpt, description) of every bracket that is not provenance, addendum, checkbox or link."""
    inicio, pos = [], 0
    for ln in text.split("\n"):
        inicio.append(pos)
        pos += len(ln) + 1

    def linha_de(p):
        import bisect
        return bisect.bisect_right(inicio, p)

    for m in COLCHETE.finditer(text):
        dentro = re.sub(r"\s+", " ", m.group(1))
        if COLCHETE_OK.match(dentro) or COLCHETE_CAIXA.match(dentro) or text[m.end():m.end() + 1] == "(":
            continue
        yield linha_de(m.start()), re.sub(r"\s+", " ", m.group(0))[:60], "colchete não preenchido — placeholder do esqueleto ou marcador interno"
    resto = COLCHETE.sub(lambda m: " " * len(m.group(0)), text)
    for m in re.finditer(r"[\[\]]", resto):
        yield linha_de(m.start()), m.group(0), "colchete sem par — placeholder aninhado ou quebrado"


def lint_text(text: str, check_extra: bool = True, perfil: str = "profissional",
              aceitas: set | None = None, final: bool = False,
              dispensas: set | None = None) -> dict:
    """Returns a dict with 'findings' (list) and 'manual_lines' (skipped lines)."""
    findings, warnings, asks, accepted, manual_lines = [], [], [], [], []
    aceitas = aceitas or set()
    dispensas = dispensas or set()
    lines = text.splitlines()

    def achado(pid, n, match, desc, line):
        findings.append({"id": pid, "line": n, "match": match, "desc": desc, "context": line.strip()})

    for n, line in enumerate(lines, start=1):
        if TOKEN_MANUAL in line:
            manual_lines.append({"line": n, "text": line.strip()})
            if final:
                achado("token_manual", n, TOKEN_MANUAL,
                       "token «manual» sobrou no documento de entrega — retirar o token (o campo em branco fica)", line)
            continue
        tem_assinatura = bool(re.search(r"assinatura", line, re.IGNORECASE))
        for pid, rx, desc in COMPILED:
            if pid in ("campo_identificador", "data_branco"):
                if perfil == "paciente" or tem_assinatura:
                    continue      # the patient's own field / signature date
            if pid == "data_branco" and any(f["line"] == n and f["id"] == "campo_identificador" for f in findings):
                continue      # line already reported as a blank field
            for m in rx.finditer(line):
                achado(pid, n, m.group(0), desc, line)
        if check_extra:
            for m in RX_HONORIFICO.finditer(line):
                achado("honorifico", n, m.group(0), "honorífico antes do nome do Felipe (regra 10)", line)
            if RX_LINHA_FELIPE.search(line):
                for m in RX_TITULO.finditer(line):
                    achado("titulo", n, m.group(0), "título de especialidade na linha do nome/CRM — só «Médico» (v3.8.14)", line)
        if perfil == "paciente":
            for pid, rx, desc in PACIENTE_PATTERNS:
                if pid == "norma_educativo" and "2.454/2026" in line:
                    continue
                for m in rx.finditer(line):
                    achado(pid, n, m.group(0), desc, line)
        if perfil == "legal" and not tem_assinatura and not SIGNATURE_LINE.match(line):
            if not any(f["line"] == n and f["id"] in ("campo_identificador", "data_branco") for f in findings):
                for m in RX_CAMPO_QUALQUER.finditer(line):
                    achado("campo_branco", n, m.group(0), "campo em branco em documento 4-E — PDF só completo", line)
        for pid, rx, desc, perfis in COMPILED_ASK:
            if perfis and perfil not in perfis:
                continue
            for m in rx.finditer(line):
                item = {"id": pid, "line": n, "match": m.group(0), "desc": desc, "context": line.strip()}
                (accepted if n in aceitas else asks).append(item)
        if SIGNATURE_LINE.match(line):
            continue
        line_blocked = any(f["line"] == n and f["id"] in ("campo_identificador", "data_branco", "campo_branco")
                           for f in findings)
        for pid, rx, desc in COMPILED_WARN:
            if line_blocked:
                break
            m = rx.search(line)
            if m:                       # one warning per line
                warnings.append({"id": pid, "line": n, "match": m.group(0),
                                 "desc": desc, "context": line.strip()})

    manuais = {x["line"] for x in manual_lines}
    for n, trecho, desc in _colchetes(text):
        if n not in manuais:
            achado("placeholder", n, trecho, desc, lines[n - 1] if 0 < n <= len(lines) else trecho)
    # marker split across two lines: "(a\nconfirmar)"
    for n in range(1, len(lines)):
        a, b = lines[n - 1].rstrip(), lines[n].lstrip()
        if TOKEN_MANUAL in a or TOKEN_MANUAL in b:
            continue
        emenda = a + " " + b
        for pid, rx, desc in COMPILED:
            if pid not in ENTRE_LINHAS:
                continue
            for m in rx.finditer(emenda):
                if m.start() < len(a) and m.end() > len(a) + 1:
                    achado(pid, n, m.group(0), desc + " (quebrada em duas linhas)", emenda)

    notas = []
    if perfil == "paciente":
        npal = len(re.findall(r"[A-Za-zÀ-ÿ]{2,}", text))
        if npal > TETO_PALAVRAS_PACIENTE:
            notas.append(f"material com {npal} palavras (referência: até {TETO_PALAVRAS_PACIENTE}) — "
                         "confira se a primeira página resolve sozinha (regra 8)")
    if final:
        findings.extend(_pecas_obrigatorias(text, perfil, dispensas, notas))
    return {"findings": findings, "asks": asks, "accepted": accepted,
            "warnings": warnings, "manual_lines": manual_lines, "notas": notas}


def lint_file(path: str, check_extra: bool = True, perfil: str = "profissional",
              aceitas: set | None = None, final: bool = False,
              dispensas: set | None = None) -> dict:
    text = read_text(path)
    result = lint_text(text, check_extra=check_extra, perfil=perfil, aceitas=aceitas,
                       final=final, dispensas=dispensas)
    result["file"] = path
    result["perfil"] = perfil
    result["final"] = final
    result["dispensas"] = sorted(dispensas or [])
    if result["findings"]:
        result["status"] = "BLOQUEADO"
    elif result["asks"]:
        result["status"] = "PERGUNTAR"
    else:
        result["status"] = "LIMPO"
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_human(result: dict) -> None:
    f = result["file"]
    st = result["status"]
    icon = {"BLOQUEADO": "✗", "PERGUNTAR": "?", "LIMPO": "✓"}[st]
    modo = "; final" if result.get("final") else "; rascunho — rode com --final antes de entregar"
    print(f"{icon} {st} — {f}  (perfil: {result.get('perfil', 'profissional')}{modo})")
    for x in result["findings"]:
        onde = f"L{x['line']:>4}" if x["line"] else "   — "
        print(f"   {onde}  [{x['id']}] {x['desc']}")
        print(f"         {x['context'][:110]}")
    if result.get("asks"):
        print(f"   ? {len(result['asks'])} ponto(s) para o FELIPE decidir — pare e pergunte, um por linha:")
        for x in result["asks"]:
            print(f"     L{x['line']:>4}  [{x['id']}] «{x['match']}» — {x['context'][:95]}")
        print("     Depois da decisão: reescrever, ou rodar de novo com --aceito-linha N (registrar no envio).")
    if result.get("accepted"):
        print(f"   ✓ {len(result['accepted'])} ponto(s) mantido(s) por decisão do Felipe: "
              + ", ".join(f"L{x['line']}" for x in result["accepted"]))
    for nota in result.get("notas", []):
        print(f"   ⓘ {nota}")
    if result.get("dispensas"):
        print("   ⓘ dispensado por decisão do Felipe (registrar no envio): " + ", ".join(result["dispensas"]))
    if result.get("warnings"):
        print(f"   ⚑ {len(result['warnings'])} aviso(s) — revisar (não bloqueia):")
        for x in result["warnings"][:8]:
            print(f"     L{x['line']:>4}  [{x['id']}] {x['context'][:100]}")
        if len(result["warnings"]) > 8:
            print(f"     … +{len(result['warnings']) - 8}")
    if result["manual_lines"] and not result.get("final"):
        print(f"   ⓘ {len(result['manual_lines'])} linha(s) com {TOKEN_MANUAL} ignorada(s) — "
              f"REMOVER o token antes de entregar:")
        for x in result["manual_lines"]:
            print(f"     L{x['line']:>4}  {x['text'][:100]}")


def main(argv: list[str]) -> int:
    args, flags, perfil, aceitas, dispensas = [], set(), "profissional", set(), set()
    it = iter(argv)
    for a in it:
        if a == "--perfil":
            perfil = next(it, "")
        elif a == "--aceito-linha":
            aceitas.add(int(next(it, "0")))
        elif a == "--dispensa":
            dispensas.add(next(it, "").lower())
        elif a.startswith("--"):
            flags.add(a)
        else:
            args.append(a)
    if "--version" in flags:
        print(VERSION)
        return 0
    if perfil not in PERFIS:
        print(f"perfil inválido: {perfil!r} — use {PERFIS}", file=sys.stderr)
        return 2
    if dispensas - {"cpf", "concordancia"}:
        print(f"dispensa inválida: {sorted(dispensas)} — use cpf ou concordancia", file=sys.stderr)
        return 2
    if not args:
        print(__doc__)
        return 2
    as_json = "--json" in flags
    check_extra = "--no-extra" not in flags
    final = "--final" in flags

    results = []
    exit_code = 0
    for path in args:
        try:
            r = lint_file(path, check_extra=check_extra, perfil=perfil, aceitas=aceitas,
                          final=final, dispensas=dispensas)
        except Ilegivel as e:
            msg = (f"✗ ILEGÍVEL — {path}: {e}. O lint NÃO leu este arquivo — não é LIMPO. "
                   f"Fazer a varredura manual da regra 4-D e dizer que o lint não rodou.")
            if as_json:
                results.append({"file": path, "status": "ILEGIVEL", "erro": str(e)})
            else:
                print(msg)
            print(msg, file=sys.stderr)
            exit_code = 2
            continue
        except Exception as e:  # noqa: BLE001
            print(f"erro ao ler {path}: {e}", file=sys.stderr)
            return 2
        results.append(r)
        if r["status"] == "BLOQUEADO" and exit_code != 2:
            exit_code = 1
        elif r["status"] == "PERGUNTAR" and exit_code == 0:
            exit_code = 3
        if not as_json:
            _print_human(r)
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
