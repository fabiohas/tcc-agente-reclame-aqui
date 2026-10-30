import json

import duckdb
import pandas as pd
import pytest

import config
from pipeline import bronze, ouro, prata
from pipeline.qualidade import ErroQualidade
from tests.conftest import linha


def escrever_csv(nome, linhas):
    config.LANDING_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(linhas).to_csv(config.LANDING_DIR / nome, index=False)


def rodar_bronze():
    return bronze.ingerir(config.LANDING_DIR, config.BRONZE_DIR, config.MANIFESTO_BRONZE)


def rodar_prata(**kw):
    return prata.gerar(config.BRONZE_DIR, config.PRATA_PARQUET, config.QUARENTENA_DIR, config.QUALIDADE_DIR,
                       hoje=pd.Timestamp("2026-10-01"), **kw)


def test_bronze_e_incremental_e_idempotente():
    escrever_csv("a.csv", [linha(1, "2026-09-01")])
    assert rodar_bronze()["arquivos_novos"] == ["a.csv"]
    assert rodar_bronze() == {"arquivos_novos": [], "arquivos_ja_ingeridos": 1, "total_arquivos": 1}

    escrever_csv("b.csv", [linha(2, "2026-09-02")])
    assert rodar_bronze()["arquivos_novos"] == ["b.csv"]
    assert len(bronze.ler_bronze(config.BRONZE_DIR)) == 2


def test_bronze_recusa_arquivo_sem_colunas_obrigatorias():
    escrever_csv("ruim.csv", [{"titulo": "x", "ID": "1"}])
    with pytest.raises(ValueError, match="colunas ausentes"):
        rodar_bronze()


def test_prata_deduplica_mantendo_a_versao_mais_recente():
    escrever_csv("a.csv", [linha(1, "2026-09-01", titulo="Versão antiga do título")])
    rodar_bronze()
    escrever_csv("b.csv", [linha(1, "2026-09-01", titulo="Versão nova do título")])
    rodar_bronze()

    relatorio = rodar_prata()
    df = pd.read_parquet(config.PRATA_PARQUET)
    assert relatorio["duplicadas_removidas"] == 1
    assert df["titulo"].tolist() == ["Versão nova do título"]


def test_prata_padroniza_e_manda_invalidas_para_quarentena():
    validas = [linha(i, "2026-09-01", cidade="  Belo   Horizonte ", estado="mg") for i in range(1, 20)]
    invalidas = [
        linha(100, "2026-09-01", estado="XX"),
        linha(101, "data ruim"),
    ]
    escrever_csv("a.csv", validas + invalidas)
    rodar_bronze()
    relatorio = rodar_prata(limite_quarentena_pct=20)

    df = pd.read_parquet(config.PRATA_PARQUET)
    assert len(df) == 19
    assert set(df["Estado"]) == {"MG"} and set(df["Cidade"]) == {"Belo Horizonte"}

    quarentena = pd.read_parquet(next(config.QUARENTENA_DIR.glob("*.parquet")))
    motivos = dict(zip(quarentena["ID"], quarentena["_motivos"]))
    assert "UF inválida" in motivos["100"]
    assert "Data" in motivos["101"]
    assert relatorio["status"] == "aprovado" and relatorio["lote"]["quarentena"] == 2


def test_prata_reprova_lote_com_quarentena_alta_e_preserva_versao_anterior():
    escrever_csv("a.csv", [linha(1, "2026-09-01")])
    rodar_bronze()
    rodar_prata()
    antes = pd.read_parquet(config.PRATA_PARQUET)

    escrever_csv("b.csv", [linha(i, "2026-09-01", estado="XX") for i in range(2, 10)])
    rodar_bronze()
    with pytest.raises(ErroQualidade):
        rodar_prata()

    pd.testing.assert_frame_equal(pd.read_parquet(config.PRATA_PARQUET), antes)
    relatorios = [json.loads(p.read_text()) for p in config.QUALIDADE_DIR.glob("*.json")]
    assert any(r["status"] == "reprovado" for r in relatorios)


def test_prata_rejeita_data_no_futuro():
    escrever_csv("a.csv", [linha(i, "2026-09-01") for i in range(1, 20)] + [linha(99, "2030-01-01")])
    rodar_bronze()
    rodar_prata(limite_quarentena_pct=20)
    quarentena = pd.read_parquet(next(config.QUARENTENA_DIR.glob("*.parquet")))
    assert "data no futuro" in quarentena["_motivos"].iloc[0]


def test_prata_anonimiza_textos():
    escrever_csv("a.csv", [linha(1, "2026-09-01", texto="Carro placa TZI3D48 quebrou na estrada ontem.")])
    rodar_bronze()
    relatorio = rodar_prata()
    assert pd.read_parquet(config.PRATA_PARQUET)["Texto_Reclamacao"].iloc[0] == "Carro placa [PLACA] quebrou na estrada ontem."
    assert relatorio["dados_pessoais_mascarados"] == {"[PLACA]": 1}


def test_ouro_cria_tabelas_e_historico():
    escrever_csv("a.csv", [linha(1, "2026-09-01", estado="SP"), linha(2, "2026-09-03", estado="MG")])
    rodar_bronze()
    rodar_prata(limite_quarentena_pct=100)
    for _ in range(2):
        ouro.gerar(config.PRATA_PARQUET, config.OURO_DUCKDB, config.OURO_PARQUET)

    with duckdb.connect(str(config.OURO_DUCKDB), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM reclamacoes").fetchone()[0] == 2
        # 3 dias x 2 UFs, incluindo os dias sem reclamação
        diarias = con.execute("SELECT Data::VARCHAR, Estado, quantidade FROM reclamacoes_diarias").fetchall()
        assert len(diarias) == 6 and ("2026-09-02", "SP", 0) in diarias
        assert con.execute("SELECT count(*) FROM execucoes").fetchone()[0] == 2

    df = ouro.ler_reclamacoes(config.OURO_PARQUET)
    assert {"Mes", "Semana", "DiaSemana", "n_palavras"} <= set(df.columns)
    assert df.loc[df["ID"] == "1", "DiaSemana"].item() == "terça"
