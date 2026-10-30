"""Corretores das respostas: números, ordem de rótulos, IDs citados e modelo juiz."""

import json
import re
import time
import unicodedata

import anthropic

import config
import telemetria

_DATAS = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
# 1.234,56 | 1.234 | 3,35 | 3.35 | 42
_NUMERO = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+|\d+\.\d+|\d+")


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").casefold()


def extrair_numeros(texto: str) -> list[float]:
    """Números da resposta em formato brasileiro ou americano. Datas são ignoradas."""
    numeros = []
    for bruto in _NUMERO.findall(_DATAS.sub(" ", texto)):
        if "," in bruto:
            valor = bruto.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", bruto):
            valor = bruto.replace(".", "")  # separador de milhar
        else:
            valor = bruto
        numeros.append(float(valor))
    return numeros


def conferir_numeros(resposta: str, esperados: list[float], tolerancia_relativa: float = 0.0) -> list[str]:
    """Falhas: números esperados que não aparecem na resposta (dentro da tolerância)."""
    encontrados = extrair_numeros(resposta)
    falhas = []
    for esperado in esperados:
        margem = max(abs(esperado) * tolerancia_relativa, 1e-9)
        if not any(abs(n - esperado) <= margem for n in encontrados):
            falhas.append(f"número {esperado:g} não encontrado")
    return falhas


def conferir_ordem(resposta: str, rotulos: list[str]) -> list[str]:
    """Falhas: rótulos ausentes ou fora da ordem esperada (pela primeira menção)."""
    texto = _sem_acento(resposta)
    posicoes = []
    for rotulo in rotulos:
        m = re.search(rf"(?<!\w){re.escape(_sem_acento(rotulo))}(?!\w)", texto)
        if not m:
            return [f"'{rotulo}' não aparece"]
        posicoes.append(m.start())
    if posicoes != sorted(posicoes):
        return [f"ordem esperada {rotulos}"]
    return []


def conferir_ids(resposta: str, ids_validos: set[str]) -> list[str]:
    citados = set(re.findall(r"\b\d{9}\b", resposta))
    if not citados:
        return ["nenhum ID citado"]
    inventados = sorted(citados - ids_validos)
    return [f"IDs que não existem na base: {inventados}"] if inventados else []


SCHEMA_JUIZ = {
    "type": "object",
    "properties": {"aprovado": {"type": "boolean"}, "justificativa": {"type": "string"}},
    "required": ["aprovado", "justificativa"],
    "additionalProperties": False,
}

SYSTEM_JUIZ = """Você avalia respostas de um agente que analisa reclamações de clientes.
Compare a resposta com o critério e a referência. Aprove só se a resposta atender ao critério.
Justifique em uma ou duas frases, em português."""


def julgar(pergunta: str, resposta: str, criterio: str, client: anthropic.Anthropic | None = None) -> tuple[bool, str, dict]:
    client = client or anthropic.Anthropic()
    inicio = time.monotonic()
    r = client.messages.create(
        model=config.MODELO_JUIZ,
        max_tokens=4000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA_JUIZ}},
        system=SYSTEM_JUIZ,
        messages=[{
            "role": "user",
            "content": f"<pergunta>{pergunta}</pergunta>\n<criterio>{criterio}</criterio>\n<resposta>{resposta}</resposta>",
        }],
    )
    registro = telemetria.registrar("juiz", r, time.monotonic() - inicio)
    if r.stop_reason != "end_turn":
        return False, f"juiz não concluiu (stop_reason={r.stop_reason})", registro
    veredito = json.loads(next(b.text for b in r.content if b.type == "text"))
    return bool(veredito["aprovado"]), veredito["justificativa"], registro
