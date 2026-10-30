"""Casos de teste do agente.

O gabarito de cada caso é calculado com pandas direto da tabela ouro, sem passar pelas
ferramentas do agente: se uma ferramenta tiver bug, a avaliação pega. Como o gabarito é
calculado na hora, os casos continuam válidos quando chegam dados novos.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Caso:
    id: str
    categoria: str
    pergunta: str
    # Números que precisam aparecer na resposta (calculados a partir dos dados).
    numeros: Callable[[pd.DataFrame], list[float]] | None = None
    tolerancia_relativa: float = 0.0
    # Rótulos que precisam aparecer na resposta, nesta ordem (ex.: ranking de estados).
    ordem: Callable[[pd.DataFrame], list[str]] | None = None
    # Critério para o modelo juiz (perguntas abertas); recebe os dados para montar a referência.
    rubrica: Callable[[pd.DataFrame], str] | None = None
    # Pelo menos uma destas ferramentas precisa ter sido usada.
    ferramentas: list[str] = field(default_factory=list)
    # Todos os IDs citados na resposta precisam existir na base (detecta IDs inventados).
    checar_ids: bool = False


def _ranking(serie: pd.Series, n: int) -> list[str]:
    contagem = serie.value_counts()
    return list(contagem.sort_index().sort_values(ascending=False, kind="stable").head(n).index)


def _entre(df, inicio, fim):
    return df[(df["Data"] >= inicio) & (df["Data"] <= fim)]


def _ultimas_semanas_ref(df: pd.DataFrame) -> str:
    fim = df["Data"].max()
    a = len(_entre(df, fim - pd.Timedelta(days=13), fim))
    b = len(_entre(df, fim - pd.Timedelta(days=14), fim))
    return (
        f"Hoje é {(fim + pd.Timedelta(days=1)).date()} e os dados vão até {fim.date()}. "
        f"As últimas 2 semanas têm {a} reclamações se contadas como os 14 dias até {fim.date()}, "
        f"ou {b} se incluírem {(fim - pd.Timedelta(days=14)).date()}. Aceite qualquer uma das duas, "
        "desde que o período usado esteja claro na resposta."
    )


CASOS: list[Caso] = [
    Caso("total", "contagem", "Quantas reclamações existem no total na base?",
         numeros=lambda df: [len(df)], ferramentas=["contar_reclamacoes"]),
    Caso("top3_estados", "ranking", "Quais são os 3 estados com mais reclamações?",
         ordem=lambda df: _ranking(df["Estado"], 3), ferramentas=["contar_reclamacoes"]),
    Caso("top_cidade", "ranking", "Qual cidade tem mais reclamações e quantas são?",
         ordem=lambda df: _ranking(df["Cidade"], 1),
         numeros=lambda df: [int(df["Cidade"].value_counts().max())]),
    Caso("qtd_mg", "contagem", "Quantas reclamações vieram de Minas Gerais?",
         numeros=lambda df: [int((df["Estado"] == "MG").sum())]),
    Caso("pct_sp", "contagem", "Qual o percentual de reclamações do estado de São Paulo sobre o total?",
         numeros=lambda df: [round(100 * (df["Estado"] == "SP").mean(), 1)], tolerancia_relativa=0.01),
    Caso("setembro", "tempo", "Quantas reclamações foram registradas em setembro de 2026?",
         numeros=lambda df: [int((df["Mes"] == "2026-09").sum())]),
    Caso("media_diaria", "tempo", "Qual a média diária de reclamações entre 01/09/2026 e 26/09/2026?",
         numeros=lambda df: [round(len(_entre(df, pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-26"))) / 26, 2)],
         tolerancia_relativa=0.01),
    Caso("dia_semana", "tempo", "Em qual dia da semana há mais reclamações? Quantas?",
         ordem=lambda df: _ranking(df["DiaSemana"], 1),
         numeros=lambda df: [int(df["DiaSemana"].value_counts().max())]),
    Caso("semana_pico", "tempo", "Qual semana (de segunda a domingo) teve mais reclamações e quantas foram?",
         numeros=lambda df: [int(df["Semana"].value_counts().max())]),
    Caso("termo_literal", "texto", "Quantas reclamações mencionam literalmente a expressão 'carro reserva'?",
         numeros=lambda df: [int((df["titulo"] + " " + df["Texto_Reclamacao"])
                                 .str.contains("carro reserva", case=False, regex=False).sum())]),
    Caso("cidade_periodo", "contagem",
         "Quantas reclamações da cidade de São Paulo houve entre 01/09/2026 e 15/09/2026?",
         numeros=lambda df: [int(((df["Cidade"] == "São Paulo")
                                  & df["Data"].between("2026-09-01", "2026-09-15")).sum())]),
    Caso("fora_do_periodo", "robustez", "Quantas reclamações foram registradas em outubro de 2026?",
         rubrica=lambda df: (
             f"Os dados vão só até {df['Data'].max().date()}, então não há reclamações de outubro de 2026 "
             "na base. A resposta deve dizer isso (zero ou sem dados), sem inventar números."
         )),
    Caso("periodo_relativo", "robustez", "Quantas reclamações houve nas últimas 2 semanas?",
         rubrica=_ultimas_semanas_ref),
    Caso("media_palavras", "codigo", "Qual a média de palavras por texto de reclamação (só o texto, sem o título)?",
         numeros=lambda df: [round(df["n_palavras"].mean(), 1)], tolerancia_relativa=0.02),
    Caso("principais_problemas", "aberta",
         "Quais são os principais tipos de problema relatados? Cite exemplos com o ID da reclamação.",
         rubrica=lambda df: (
             "A resposta deve listar pelo menos 3 tipos de problema plausíveis para reclamações de "
             "aluguel/assinatura de carros (ex.: cobrança, manutenção, atendimento, devolução) e citar "
             "exemplos com ID. Reprove se for genérica demais ou não citar nenhum ID."
         ),
         checar_ids=True),
    Caso("assunto_carro_reserva", "assunto", "Quantas reclamações tratam de problemas com carro reserva?",
         rubrica=lambda df: (
             "A resposta deve trazer um número de reclamações sobre problemas com carro reserva e "
             "deixar claro como chegou nele (ex.: um modelo leu cada reclamação). Reprove se não der número."
         ),
         ferramentas=["contar_por_criterio"]),
]
