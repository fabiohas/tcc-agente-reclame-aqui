import pytest

from avaliacao import corretores
from avaliacao.casos import CASOS, Caso
from avaliacao.rodar import corrigir


@pytest.mark.parametrize("texto, esperado", [
    ("Foram 1.234 reclamações", [1234]),
    ("média de 3,35 por dia", [3.35]),
    ("42,4% do total", [42.4]),
    ("total de 1.234,5", [1234.5]),
    ("média 3.35", [3.35]),
    ("em 14/09 a 20/09 houve 28", [28]),       # datas não contam
    ("de 2026-09-01 até hoje: 87", [87]),
])
def test_extrair_numeros(texto, esperado):
    assert corretores.extrair_numeros(texto) == esperado


def test_conferir_numeros_com_tolerancia():
    assert corretores.conferir_numeros("média de 3,3 por dia", [3.35], 0.02) == []
    assert corretores.conferir_numeros("média de 3,0 por dia", [3.35], 0.02) != []


def test_conferir_ordem_ignora_acentos_e_exige_ordem():
    assert corretores.conferir_ordem("1º SP, 2º MG, 3º RJ", ["SP", "MG", "RJ"]) == []
    assert corretores.conferir_ordem("MG lidera, depois SP", ["SP", "MG"]) != []
    assert corretores.conferir_ordem("Sao Paulo lidera", ["São Paulo"]) == []
    assert corretores.conferir_ordem("Espírito Santo", ["SP"]) != []  # não casa dentro de outra palavra


def test_conferir_ids_detecta_ids_inventados():
    validos = {"260116891"}
    assert corretores.conferir_ids("ver 260116891", validos) == []
    assert "não existem" in corretores.conferir_ids("ver 260116891 e 123456789", validos)[0]
    assert corretores.conferir_ids("sem exemplos", validos) == ["nenhum ID citado"]


def test_corrigir_combina_criterios(df_ouro):
    caso = Caso("x", "teste", "?", numeros=lambda df: [len(df)], ferramentas=["contar_reclamacoes"],
                rubrica=lambda df: "critério")
    juiz = lambda p, r, c: (True, "ok", {"custo_usd": 0.001})
    assert corrigir(caso, df_ouro, "São 6.", ["contar_reclamacoes"], juiz)[0] == []
    falhas, _ = corrigir(caso, df_ouro, "São 5.", ["buscar_reclamacoes"], juiz)
    assert len(falhas) == 2


def test_gabaritos_calculam_sem_erro(df_ouro):
    for caso in CASOS:
        for f in (caso.numeros, caso.ordem, caso.rubrica):
            if f:
                f(df_ouro)
    assert len({c.id for c in CASOS}) == len(CASOS)
