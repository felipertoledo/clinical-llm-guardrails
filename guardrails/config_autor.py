#!/usr/bin/env python3
"""config_autor.py — identity of whoever uses the tool.

The ONLY file with name, licence number, clinic and local network. The
linter and the tracer read from here; a colleague's edition swaps this file
only. Nothing here is inferred: address and contact stay None until the
physician supplies them.
"""
import re
import unicodedata

NOME = "Felipe Ribeiro Toledo"
TITULO = "Médico"
REGISTRO = "CRM-SP 216.986"
REGISTRO_NUM = "216.986"            # counts only whole in the tracer
MONOGRAMA = "FRT"
CONSULTORIO = "Clínica Ribeiro Toledo"
UNIDADE = 'USF 5 "José Pascoal Rosolim"'
UNIDADE_CURTA = "USF 5"
MUNICIPIO_UF = "Estiva Gerbi/SP"

ASSINATURA = f"{NOME} — {TITULO} — {REGISTRO}"

# Header for each setting (rule 1). Address/contact stay None until the
# physician supplies them — never inferred.
CONTEXTOS = {
    "usf": {"nome": f"{UNIDADE} — {MUNICIPIO_UF}", "endereco": None, "contato": None},
    "crt": {"nome": CONSULTORIO, "endereco": None, "contato": None},
}

# Proper names the framework supplies (local network) — the tracer does not
# require an origin for them in the source, except in identification fields.
REDE = [
    UNIDADE_CURTA, "José Pascoal Rosolim", "Estiva Gerbi", CONSULTORIO, "Mogi Guaçu",
]


def _sem_acento(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")


def _rx_compacto(s: str) -> str:
    """'CRM-UF 123.456' → 'crm-?uf123\\.?456' (no spaces; hyphen and dot optional)."""
    base = re.escape(_sem_acento(s).lower().replace(" ", ""))
    return base.replace(r"\-", "-?").replace(r"\.", r"\.?")


_partes = NOME.split()
_SEP = r"[-•·|,]?"
# signature in compacted text (lower case, no accents, no spaces)
RX_ASSINATURA_COMPACTA = _rx_compacto(NOME) + _SEP + _rx_compacto(TITULO) + _SEP + _rx_compacto(REGISTRO)
# "Dr." before the author's name (rule 10): first name, initials + last, or compound surname
RX_NOME_HONORIFICO = ("(?:" + re.escape(_partes[0].lower()) + "|"
                      + r"\s*".join(re.escape(p[0].lower()) + r"\." for p in _partes[:-1]) + r"\s*" + re.escape(_partes[-1].lower())
                      + "|" + r"\s+".join(re.escape(p.lower()) for p in _partes[1:]) + ")")
# name/licence line (where the title is checked)
RX_LINHA_AUTOR = (r"\s+".join(re.escape(p.lower()) for p in _partes) + "|"
                  + re.escape(_sem_acento(REGISTRO).lower()).replace(r"\-", r"-?\s?").replace(r"\ ", r"\s*").replace(r"\.", r"\.?"))
RX_CABECALHO = "|".join([re.escape(UNIDADE_CURTA), re.escape(_sem_acento(CONSULTORIO)).replace("Clinica", "Cl[íi]nica"), re.escape(NOME)])
