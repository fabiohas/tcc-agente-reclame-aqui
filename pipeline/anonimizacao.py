"""Mascaramento de dados pessoais nos textos (LGPD).

As reclamações são públicas, mas trazem dados que identificam pessoas ou veículos. Eles não
ajudam nas análises, então são trocados por marcadores antes de sair da camada bronze.
"""

import re

# Ordem importa: padrões mais específicos primeiro (CPF/CNPJ antes de telefone).
PADROES: list[tuple[str, re.Pattern]] = [
    ("[EMAIL]", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("[CNPJ]", re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")),
    # CPF só formatado ou logo após a palavra "CPF": 11 dígitos soltos costumam ser protocolos.
    ("[CPF]", re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|(?<=CPF)[\s:nº°.-]*\d{11}\b", re.IGNORECASE)),
    # Telefones brasileiros: (31) 99999-9999, 31 3333-3333, +55 31 99999 9999...
    ("[TELEFONE]", re.compile(r"(?:\+?55\s?)?\(?\b\d{2}\)?\s?9?\d{4}[-\s]\d{4}\b")),
    # Contratos da Localiza (GVM9503/25): antes da placa, que tem formato parecido.
    ("[CONTRATO]", re.compile(r"\b[A-Z]{3}\d{4}/\d{2}\b")),
    # Placas: Mercosul (ABC1D23) e antiga (ABC-1234 / ABC1234).
    ("[PLACA]", re.compile(r"\b[A-Z]{3}-?\d[A-Z0-9]\d{2}\b", re.IGNORECASE)),
]


def anonimizar(texto: str) -> str:
    if not isinstance(texto, str):
        return texto
    for marcador, padrao in PADROES:
        texto = padrao.sub(marcador, texto)
    return texto


def contar_ocorrencias(texto: str) -> dict[str, int]:
    """Quantos dados de cada tipo o texto tem (usado no relatório de qualidade)."""
    if not isinstance(texto, str):
        return {}
    contagem = {}
    for marcador, padrao in PADROES:
        n = len(padrao.findall(texto))
        if n:
            contagem[marcador] = n
        texto = padrao.sub(marcador, texto)
    return contagem
