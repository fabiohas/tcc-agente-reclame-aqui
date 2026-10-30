import json

import pytest

import ferramentas as modulo
from ferramentas import Ferramentas


@pytest.fixture
def f(df_ouro, tmp_path, monkeypatch):
    monkeypatch.setattr(modulo, "CACHE_CRITERIOS", tmp_path / "cache.json")
    return Ferramentas(df_ouro, indice=None)


def rodar(f, nome, **entrada):
    return json.loads(f.executar(nome, entrada))


def test_total_e_ranking_com_empate_no_corte(f):
    r = rodar(f, "contar_reclamacoes", agrupar_por="Estado", top_n=2)
    assert r["total"] == 6
    assert [(g["valor"], g["quantidade"]) for g in r["grupos"]] == [("MG", 2), ("SP", 2)]
    assert r["empatados_no_corte"] == 2
    assert r["grupos"][0]["percentual"] == pytest.approx(33.3)


def test_dias_sem_reclamacao_entram_com_zero(f):
    r = rodar(f, "contar_reclamacoes", agrupar_por="Data")
    por_dia = {g["valor"]: g["quantidade"] for g in r["grupos"]}
    assert len(por_dia) == 10 and por_dia["2026-09-05"] == 0
    assert r["estatisticas"]["media"] == pytest.approx(0.6)
    assert list(por_dia) == sorted(por_dia)  # ordem cronológica por padrão


def test_semana_marca_semanas_parciais(f):
    r = rodar(f, "contar_reclamacoes", agrupar_por="Semana")
    assert [(g["valor"], g["quantidade"], g["semana_parcial"]) for g in r["grupos"]] == [
        ("2026-08-31", 4, True),   # dados começam na terça 01/09
        ("2026-09-07", 2, True),   # dados terminam na quinta 10/09
    ]


def test_dia_da_semana_em_ordem_de_calendario(f):
    r = rodar(f, "contar_reclamacoes", agrupar_por="DiaSemana")
    assert [g["valor"] for g in r["grupos"]] == ["terça", "quarta", "quinta"]


def test_filtros(f):
    assert rodar(f, "contar_reclamacoes", cidade="são paulo")["total"] == 1
    assert rodar(f, "contar_reclamacoes", estado="sp", data_fim="2026-09-01")["total"] == 2
    assert rodar(f, "contar_reclamacoes", contem_termo="carro reserva")["total"] == 2


def test_agrupamento_invalido(f):
    with pytest.raises(ValueError):
        f.executar("contar_reclamacoes", {"agrupar_por": "Bairro"})


def test_obter_reclamacao(f):
    assert rodar(f, "obter_reclamacao", id="3")["cidade"] == "Belo Horizonte"
    with pytest.raises(ValueError):
        f.executar("obter_reclamacao", {"id": "999"})


def test_contar_por_criterio_classifica_cada_uma_e_usa_cache(f, monkeypatch):
    chamadas = []

    def falso(criterio, r):
        chamadas.append(r["ID"])
        atende = "reserva" in r["Texto_Reclamacao"].lower()
        return {"atende": atende, "justificativa": "cita carro reserva" if atende else "não cita"}

    monkeypatch.setattr(f, "_classificar", falso)
    r = rodar(f, "contar_por_criterio", criterio="problema com carro reserva", agrupar_por="Estado")
    assert (r["avaliadas"], r["atendem"]) == (6, 2)
    assert {g["valor"] for g in r["grupos"]} == {"SP", "RJ"}
    assert {x["id"] for x in r["reclamacoes"]} == {"1", "5"}
    assert len(chamadas) == 6

    # Mesmo critério (com espaços/maiúsculas diferentes): tudo vem do cache.
    rodar(f, "contar_por_criterio", criterio="  Problema com   carro reserva ")
    assert len(chamadas) == 6


def test_contar_por_criterio_informa_nao_classificadas(f, monkeypatch):
    monkeypatch.setattr(f, "_classificar", lambda c, r: None if r["ID"] == "1" else {"atende": False, "justificativa": "-"})
    r = rodar(f, "contar_por_criterio", criterio="qualquer")
    assert r["nao_classificadas"] == 1 and r["atendem"] == 0


def test_contar_por_criterio_respeita_limite(f, monkeypatch):
    monkeypatch.setattr(modulo, "MAX_CLASSIFICACOES", 3)
    with pytest.raises(ValueError, match="limite"):
        f.executar("contar_por_criterio", {"criterio": "x"})
