#!/usr/bin/env python3
"""
rastreia_campos.py — field traceability ("where did this come from?") — v2.1

Question it answers: does EVERY concrete datum in the document come from
somewhere? Each identifier, date, number, dose, dosing interval and
duration, age, time-since-onset, ICD code and regulation number that appears
in the generated document must exist in the SOURCE — the physician's
messages, copied verbatim, plus the attachments of the conversation — or in
the framework's canonical list.

Complements lint_4d.py: the linter catches DOUBT MARKERS; this catches DATA
WITH NO ORIGIN (the city that was deduced, the birth year that was picked,
the ICD code nobody gave, the swapped dose, the "8/8 h" that became
"12/12 h").

Target language: Brazilian Portuguese clinical documents. The patterns and
the runtime messages are in Portuguese on purpose.

Usage:
    python rastreia_campos.py --fonte source.txt document.pdf
    python rastreia_campos.py --fonte source.txt --fonte /path/report.pdf doc.md
    python rastreia_campos.py --fonte source.txt --transcricao visit.md doc.pdf
    python rastreia_campos.py --json --fonte source.txt doc.md

SOURCE: source.txt = the physician's messages copied verbatim, appended at
every message (never a summary, never a rewrite). A confirmation question
answered "yes" goes in as the question line + "→ sim". An ATTACHMENT (lab
report, imaging) goes in by its own file path (.pdf, .docx, .txt, .md,
.html) — never retyped. A visit TRANSCRIPT goes in with --transcricao: it
supports numbers, doses and dates, but NEVER identification fields or
places — a name heard during the visit does not become a document field.

v2.0 — comparison by TOKEN, not by substring: "5 mg" no longer passes
because the source has "25 mg"; "1958" no longer passes because the source
has "219, 58 kg"; a bare year no longer passes via the canonical list.
One- and two-digit numbers are checked when they are dosing (every N hours,
N/N h, N times a day, for N days), age or time-since-onset.

DECLARED LIMIT: the program checks that the number EXISTS in the source, not
that it belongs to THAT drug or THAT fact (losartan's 50 mg placed on
amlodipine passes). Binding a value to an item is the verifier's job.

Exit codes:
    0  RASTREADO (traced) — every field/datum has an origin
    1  BLOQUEADO (blocked) — there is data with no origin (listed with line)
    2  ILEGÍVEL (unreadable) or usage error — document or source without
       text: this is NOT green
Warnings (proper name or measurement with no origin) do not block: they go
to the verifier.
"""

import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

VERSION = "2.1.0"

# Entities and texts the framework itself supplies (author, network, regulations).
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import config_autor as _cfg  # noqa: E402

CANONICO = [_cfg.NOME, _cfg.REGISTRO, _cfg.TITULO] + _cfg.REDE + [
    "CAPS", "CAPSi", "CAPS-AD", "CRAS", "CREAS", "SUS", "SISREG", "ACS", "CVV", "SAMU",
    "Res. CFM 2.454/2026", "CFM 2.454/2026", "2.454/2026",
    "CFM 2.381/2024", "2.381/2024", "CFM 2.314/2022", "2.314/2022",
    "CFM 2.217/2018", "2.217/2018", "CFM 1.821/2007", "1.821/2007",
    "Memed", "CIAP-2", "CID-10", "SP",
]
# canonical numbers count only WHOLE (never the "2026" of "2.454/2026")
CANONICO_NUM = [_cfg.REGISTRO_NUM, "188", "192"]
CID_FALSO = {"B12", "Q10", "N95"}

ID_LABELS = (r"(?:paciente|nome(?:\s+completo)?|dn|data\s+de\s+nascimento|nascimento|"
             r"cns|cart[ãa]o\s+sus|cpf|rg|endere[çc]o|cidade|munic[íi]pio|escola|"
             r"s[ée]rie|turma|empregador|empresa|cargo|respons[áa]vel|m[ãa]e|pai|"
             r"conv[êe]nio|carteira|institui[çc][ãa]o|ilpi|destinat[áa]rio|"
             r"unidade\s+de\s+origem|idade|alun[oa])")
RX_LABEL = re.compile(r"(?:^|\|)\s*[•\-*]?\s*" + ID_LABELS + r"(?:\s+(?:de|do|da)\s+[\wÀ-ÿ]+)?\s*[:\-–—]\s*((?:(?!\s[—–|]\s)[^|;\n])+)", re.IGNORECASE)
CABECALHO = re.compile(_cfg.RX_CABECALHO, re.IGNORECASE)
RX_LOCAL = re.compile(r"\b((?:[A-ZÀ-Ý][\wÀ-ÿ']+\s+(?:d[aeo]s?\s+)?){0,3}[A-ZÀ-Ý][\wÀ-ÿ']+)\s*[-/]\s*SP\b")

MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
         "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
RX_DATA_NUM = re.compile(r"(?<![\d/.])(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})(?![\d/])")
RX_DATA_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
RX_DATA_TXT = re.compile(r"\b(\d{1,2})[ºo]?\s+de\s+([a-zç]+)\s+de\s+(\d{4})\b", re.IGNORECASE)

RX_TOK = re.compile(r"\d+(?:[.,]\d+)*")
# A number that is part of a NAME (GAD-7, PHQ-9, CIAP-2, CID-10, COVID-19, B12,
# T4, SpO2, HbA1c) is not a value: it cannot be the origin of "7 dias" or
# "12 semanas" [v2.0.1].
RX_SIGLA_NUM = re.compile(r"\b[A-Za-zÀ-ÿ]{2,}-\d{1,3}\b|\b[A-Z]{1,5}\d{1,2}\b|\bSpO2\b|\bHbA1c\b")
RX_NUM = re.compile(r"(?<![\w.,/])\d{3,}(?:[.\-/]\d+)*(?![\w])")
RX_RUN = re.compile(r"\d[\d.\-/ ]{3,}\d")
UNIDADES = (r"mg/kg|mg/ml|mcg|µg|ug|mg|ml|gramas?|gotas?|gts|comprimidos?|comp\.?|cps?|"
            r"c[áa]psulas?|caps\.?|jatos?|sach[êe]s?|ampolas?|ui|g|%")
RX_DOSE = re.compile(r"(?<![\w.,/])(\d+(?:[.,]\d+)*)\s?(" + UNIDADES + r")(?![\wÀ-ÿ])", re.IGNORECASE)
RX_CID = re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d{1,2})?\b")
RX_NORMA = re.compile(r"\b(?:Resolu[çc][ãa]o|Res\.|Lei|Portaria|RDC|Nota\s+T[ée]cnica|Decreto)"
                      r"[^\n]{0,20}?(?:n[º°o.]?\s*)?(\d[\d.]*(?:/\d{2,4})?)", re.IGNORECASE)
RX_NOME = re.compile(r"(?<![.:!?]\s)(?<!^)\b([A-ZÀ-Ý][a-zà-ÿ]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ý][a-zà-ÿ]+)+)")
SIGNATURE_LINE = re.compile(r"^\s*_{5,}\s*$")
VAZIO = re.compile(r"^[\s_./\-–—:]*$")

# Dosing (blocks): the number must exist in the source.
_H = r"(?:h\b|hs\b|horas?\b)"
_T = r"(?:dias?|semanas?|meses|m[êe]s|anos?|horas?)"
RX_POSOLOGIA = [
    re.compile(r"\bde\s+(\d{1,2})\s+em\s+(\d{1,2})\s*" + _H, re.IGNORECASE),
    re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})\s*" + _H, re.IGNORECASE),
    re.compile(r"\ba\s+cada\s+(\d{1,2})\s*(?:" + _H + r"|dias?\b|semanas?\b)", re.IGNORECASE),
    re.compile(r"(?<![\w.,/])(\d{1,2})\s*(?:x|vez(?:es)?)\s*(?:ao|por|/|a)\s*(?:dia|semana|m[êe]s)\b", re.IGNORECASE),
    re.compile(r"\b(?:por|durante)\s+(\d{1,3})\s+(?:dias?|semanas?|meses|m[êe]s)\b", re.IGNORECASE),
]
RX_TEMPO = re.compile(r"\bh[áa]\s+(?:cerca\s+de\s+|aproximadamente\s+|uns\s+|umas\s+|mais\s+de\s+|"
                      r"menos\s+de\s+|quase\s+|~\s*)?(\d{1,3})\s+" + _T + r"\b", re.IGNORECASE)
RX_IDADE = re.compile(r"(?:\b(?:paciente|alun[oa]|menin[oa]|crian[çc]a|adolescente|lactente|gestante|"
                      r"idos[oa]|senhora?|sra?\.?|dona|seu|homem|mulher|filh[oa])\b[^.\n]{0,40}?|"
                      r"\btem\s+|\baos\s+|[—–]\s*|,\s*)(\d{1,3})\s*anos\b", re.IGNORECASE)
RX_MEDIDA = re.compile(r"(?<![\w.,/])(\d{1,2}(?:[.,]\d+)?)\s*(kg|cm|mmhg|bpm|irpm|rpm|[°º]c|litros?|"
                       r"minutos?|min|horas?|hs?|dias?|semanas?|meses|m[êe]s|anos?)\b", re.IGNORECASE)

NUMERAIS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
            "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13,
            "quatorze": 14, "catorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17,
            "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30, "quarenta": 40,
            "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90, "cem": 100}
_DEZ = "vinte|trinta|quarenta|cinquenta|sessenta|setenta|oitenta|noventa"
_UNI = "um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove"
RX_NUMERAL = re.compile(r"\b(?:(" + _DEZ + r")\s+e\s+(" + _UNI + r")|(" + "|".join(NUMERAIS) + r"))\b")


class Ilegivel(Exception):
    """Document or source without text the program can read."""


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def nnum(s: str) -> str:
    """Canonical form of a number (pt-BR notation): 1.500 → 1500 · 0,50 → 0.5 · 05 → 5."""
    s = s.strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):
        s = s.replace(".", "")
    s = s.replace(",", ".")
    if s.count(".") > 1:
        s = s.replace(".", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return re.sub(r"^0+(?=\d)", "", s) or "0"


def unidade(u: str) -> str:
    """Unit class: comprimidos/comp/cp → cp · µg/ug/mcg → mcg · gramas → g."""
    bruto = u.strip().lower().rstrip(".")
    if bruto[:1] in ("µ", "μ"):
        return "mcg"
    u = norm(bruto)
    for classe, formas in (("mcg", ("mcg", "ug")), ("cp", ("cp", "cps", "comp", "comprimido", "comprimidos")),
                           ("gota", ("gota", "gotas", "gts")), ("capsula", ("capsula", "capsulas", "caps")),
                           ("jato", ("jato", "jatos")), ("sache", ("sache", "saches")),
                           ("ampola", ("ampola", "ampolas")), ("g", ("g", "grama", "gramas"))):
        if u in formas:
            return classe
    return u


def read_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    suf = p.suffix.lower()
    if suf == ".pdf":
        try:
            import pdfplumber  # type: ignore
            with pdfplumber.open(str(p)) as pdf:
                return "\n".join((pg.extract_text() or "") for pg in pdf.pages)
        except ImportError:
            from pypdf import PdfReader  # type: ignore
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    if suf == ".docx":
        with zipfile.ZipFile(str(p)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    raw = p.read_text(encoding="utf-8", errors="replace")
    if suf in (".html", ".htm"):
        raw = re.sub(r"<[^>]+>", " ", raw)
    return raw


def checar_legivel(text: str, path: str, papel: str) -> None:
    if not text.strip():
        raise Ilegivel(f"{papel} vazio(a): {path}")
    letras = sum(ch.isalpha() for ch in text)
    if str(path).lower().endswith(".pdf") and letras < 20:
        raise Ilegivel(f"{papel} sem texto extraível (PDF digitalizado?): {path}")
    ruins = text.count("�") + len(re.findall(r"\(cid:\d+\)", text))
    if ruins > max(5, letras * 0.2):
        raise Ilegivel(f"{papel} com texto corrompido na extração: {path}")


def _datas(texto: str) -> set:
    out = set()
    for m in RX_DATA_NUM.finditer(texto):
        d, mo, a = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= d <= 31 and 1 <= mo <= 12:
            out.add((d, mo, int(a) % 100, int(a) if len(a) == 4 else None))
    for m in RX_DATA_ISO.finditer(texto):
        a, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            out.add((d, mo, a % 100, a))
    for m in RX_DATA_TXT.finditer(texto):
        mo = MESES.get(norm(m.group(2)))
        if mo:
            a = int(m.group(3))
            out.add((int(m.group(1)), mo, a % 100, a))
    return out


class Fonte:
    def __init__(self, texto: str):
        self.n = norm(texto)
        self.canon = norm(" | ".join(CANONICO))
        self.nums = {nnum(t) for t in RX_TOK.findall(RX_SIGLA_NUM.sub(" ", texto))}
        for m in RX_NUMERAL.finditer(self.n):
            if m.group(1):
                self.nums.add(str(NUMERAIS[m.group(1)] + NUMERAIS[m.group(2)]))
            else:
                self.nums.add(str(NUMERAIS[m.group(3)]))
        if re.search(r"\bmei[ao]\b", self.n):
            self.nums.add("0.5")
        self.runs = {digits(r) for r in RX_RUN.findall(texto)} | {digits(t) for t in RX_TOK.findall(texto)}
        self.canon_nums = {nnum(c) for c in CANONICO_NUM}
        self.doses = {(nnum(m.group(1)), unidade(m.group(2))) for m in RX_DOSE.finditer(texto)}
        self.datas = _datas(texto)

    def tem(self, valor: str, canonico: bool = True) -> bool:
        """Text present in the source as a whole word/expression."""
        v = norm(valor).strip(" .,;:")
        if not v:
            return True
        pat = r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])"
        return bool(re.search(pat, self.n)) or (canonico and bool(re.search(pat, self.canon)))

    def tem_num(self, valor: str) -> bool:
        """Number present as a whole token (or as a whole digit run)."""
        partes = [p for p in re.split(r"[/\-]", valor) if p]
        if partes and all(nnum(p) in self.nums for p in partes):
            return True
        d = digits(valor)
        if len(d) >= 5 and d in self.runs:
            return True
        return nnum(valor) in self.canon_nums

    def tem_local(self, valor: str) -> bool:
        # line merged by PDF extraction ("Nome: Fulano Mogi Guaçu-SP"): a suffix
        # of the place name present in the source is enough
        w = valor.split()
        return any(self.tem(" ".join(w[i:]), canonico=False) for i in range(len(w)))

    def tem_idade(self, valor: str) -> bool:
        m = re.match(r"\s*(\d{1,3})\s*(?:anos?|a)\b", valor, re.IGNORECASE)
        return bool(m) and bool(re.search(r"\b" + m.group(1) + r"\s*(?:a|anos?)\b", self.n))

    def tem_data(self, valor: str) -> bool:
        for d, mo, a2, a4 in _datas(valor):
            for fd, fmo, fa2, fa4 in self.datas:
                if (d, mo, a2) == (fd, fmo, fa2) and (a4 is None or fa4 is None or a4 == fa4):
                    return True
        return False

    def tem_campo(self, valor: str) -> bool:
        if _datas(valor):
            return self.tem_data(valor)
        if self.tem(valor, canonico=False) or self.tem_idade(valor):
            return True
        if re.search(r"[A-Za-zÀ-ÿ]{2,}", valor):
            return False
        d = digits(valor)                       # CPF/CNS (national IDs) with or without punctuation
        if len(d) >= 5 and d in self.runs:
            return True
        toks = RX_TOK.findall(valor)
        return bool(toks) and all(self.tem_num(t) for t in toks)


def rastrear(doc: str, fonte: Fonte, forte: Fonte | None = None) -> dict:
    forte = forte or fonte      # identification fields and places: strong source only
    bloqueios, avisos = [], []
    vistos = set()

    def add(lista, tipo, n, valor, linha, desc):
        chave = (tipo, norm(valor))
        if chave in vistos:
            return
        vistos.add(chave)
        lista.append({"tipo": tipo, "linha": n, "valor": valor.strip(), "desc": desc,
                      "contexto": linha.strip()[:120]})

    doc = re.sub(r"\(cid:\d+\)", " ", doc)  # PDF extraction artefact
    linhas = doc.splitlines()
    for n, linha in enumerate(linhas, start=1):
        if SIGNATURE_LINE.match(linha) or "«manual»" in linha:
            continue
        for m in RX_LABEL.finditer(linha):
            val = m.group(1).strip()
            if VAZIO.match(val) or re.match(r"_{2,}", val):
                continue      # blank field (for the reader): the linter judges it, by profile
            # identification field: ONLY the source counts — a city being in the
            # framework's network does not authorise it as the patient's city
            if not forte.tem_campo(val):
                add(bloqueios, "campo", n, val, linha, "campo de identificação sem origem na fonte")
        for m in RX_LOCAL.finditer(linha):
            if CABECALHO.search(linha):
                continue  # fixed clinic header (rule 1)
            if not forte.tem_local(m.group(1)):
                add(bloqueios, "local", n, m.group(1), linha, "município/local sem origem na fonte")
        ocupado = []                                   # spans already checked as dates
        for rx in (RX_DATA_NUM, RX_DATA_ISO, RX_DATA_TXT):
            for m in rx.finditer(linha):
                if not _datas(m.group(0)):
                    continue
                ocupado.append(m.span())
                if not fonte.tem_data(m.group(0)):
                    add(bloqueios, "data", n, m.group(0), linha, "data sem origem na fonte")
        for m in RX_NORMA.finditer(linha):
            if not fonte.tem(m.group(1)):
                add(bloqueios, "norma", n, m.group(0), linha, "número de norma sem origem (fonte ou corpus ✓)")
        for m in RX_CID.finditer(linha):
            if m.group(0) in CID_FALSO:
                continue
            if not fonte.tem(m.group(0), canonico=False):
                add(bloqueios, "cid", n, m.group(0), linha, "código CID sem origem na fonte")
        for m in RX_DOSE.finditer(linha):
            num, un = nnum(m.group(1)), unidade(m.group(2))
            if (num, un) in fonte.doses or (un == "%" and num in fonte.nums):
                continue      # "SpO2 92" in the source supports "92%"
            if num in fonte.nums:
                add(bloqueios, "dose", n, m.group(0), linha,
                    "dose: o número está na fonte, mas sem esta unidade — confirmar a unidade com o Felipe")
            else:
                add(bloqueios, "dose", n, m.group(0), linha, "dose/quantidade sem origem na fonte")
        sem_datas = list(linha)
        for a, b in ocupado:
            sem_datas[a:b] = " " * (b - a)
        sem_datas = "".join(sem_datas)
        for m in RX_NUM.finditer(sem_datas):
            if not fonte.tem_num(m.group(0)):
                add(bloqueios, "numero", n, m.group(0), linha, "número (documento, ano, medida) sem origem na fonte")
        pequenos = set()
        for rx in RX_POSOLOGIA:
            for m in rx.finditer(sem_datas):
                pequenos.add(m.span())
                for g in m.groups():
                    if g and nnum(g) not in fonte.nums:
                        add(bloqueios, "posologia", n, m.group(0), linha,
                            "intervalo/duração de posologia sem origem na fonte")
                        break
        for m in RX_TEMPO.finditer(sem_datas):
            pequenos.add(m.span())
            if nnum(m.group(1)) not in fonte.nums:
                add(bloqueios, "tempo", n, m.group(0), linha, "tempo de história sem origem na fonte")
        for m in RX_IDADE.finditer(sem_datas):
            pequenos.add(m.span())
            if nnum(m.group(1)) not in fonte.nums:
                add(bloqueios, "idade", n, m.group(1) + " anos", linha, "idade sem origem na fonte")
        for m in RX_MEDIDA.finditer(sem_datas):
            if any(a <= m.start() < b for a, b in pequenos):
                continue
            if nnum(m.group(1)) not in fonte.nums:
                add(avisos, "medida", n, m.group(0), linha, "medida/prazo sem origem — verificador julga")
        for m in RX_NOME.finditer(linha):
            if not fonte.tem(m.group(1)):
                add(avisos, "nome", n, m.group(1), linha, "nome próprio sem origem — verificador julga")
    # the SOURCE cannot be the document itself
    longas = [norm(l) for l in linhas if len(l.strip()) > 30]
    if len(longas) >= 5 and sum(l in fonte.n for l in longas) >= 0.6 * len(longas):
        avisos.append({"tipo": "fonte", "linha": 0, "valor": "", "contexto": "",
                       "desc": "a FONTE parece conter o próprio documento — ela deve ser as mensagens "
                               "do Felipe e os anexos, não o texto gerado"})
    return {"bloqueios": bloqueios, "avisos": avisos}


def main(argv):
    fontes, fracas, docs, as_json = [], [], [], False
    it = iter(argv)
    for a in it:
        if a == "--fonte":
            fontes.append(next(it, ""))
        elif a == "--transcricao":
            fracas.append(next(it, ""))
        elif a == "--json":
            as_json = True
        elif a == "--version":
            print(VERSION)
            return 0
        else:
            docs.append(a)
    if not fontes or not docs:
        print(__doc__)
        return 2
    try:
        textos = []
        for f in fontes:
            t = read_text(f)
            checar_legivel(t, f, "FONTE")
            textos.append(t)
        forte = Fonte("\n".join(textos))
        for f in fracas:
            t = read_text(f)
            checar_legivel(t, f, "TRANSCRIÇÃO")
            textos.append(t)
        fonte = Fonte("\n".join(textos)) if fracas else forte
        resultados = []
        for d in docs:
            t = read_text(d)
            checar_legivel(t, d, "DOCUMENTO")
            r = rastrear(t, fonte, forte)
            r["file"] = d
            r["status"] = "BLOQUEADO" if r["bloqueios"] else "RASTREADO"
            resultados.append(r)
    except Ilegivel as e:
        msg = (f"✗ ILEGÍVEL — {e}. O rastreio NÃO rodou — não é RASTREADO. "
               f"Conferir à mão cada dado contra a fonte e dizer que o rastreio não rodou.")
        print(msg)
        print(msg, file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"erro: {e}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
    else:
        for r in resultados:
            print(f"{'✗' if r['bloqueios'] else '✓'} {r['status']} — {r['file']}")
            for x in r["bloqueios"]:
                print(f"   L{x['linha']:>4}  [{x['tipo']}] «{x['valor']}» — {x['desc']}")
                print(f"         {x['contexto']}")
            if r["avisos"]:
                print(f"   ⚑ {len(r['avisos'])} aviso(s) — o verificador decide:")
                for x in r["avisos"][:12]:
                    onde = f"L{x['linha']:>4}" if x["linha"] else "   — "
                    print(f"     {onde}  [{x['tipo']}] «{x['valor']}» {x['desc'] if x['tipo'] == 'fonte' else ''}".rstrip())
    return 1 if any(r["bloqueios"] for r in resultados) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
