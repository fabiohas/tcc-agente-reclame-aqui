"""Camada ouro: tabelas prontas para consumo, num banco DuckDB e num Parquet para o app.

Tabelas:
- reclamacoes: uma linha por reclamação, com colunas derivadas (mês, semana, dia da semana, nº de palavras);
- reclamacoes_diarias: quantidade por dia e UF, incluindo dias sem reclamação (para séries temporais);
- execucoes: histórico das execuções do pipeline (linhagem e auditoria).

O banco é escrito num arquivo temporário e trocado de uma vez (os leitores nunca veem um banco pela metade).
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

import config

log = logging.getLogger(__name__)

DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def enriquecer(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Data"] = pd.to_datetime(df["Data"]).dt.normalize()
    df["Mes"] = df["Data"].dt.strftime("%Y-%m")
    df["Semana"] = df["Data"].dt.to_period("W-SUN").dt.start_time.dt.strftime("%Y-%m-%d")
    df["DiaSemana"] = df["Data"].dt.dayofweek.map(lambda i: DIAS_SEMANA[i])
    df["n_palavras"] = df["Texto_Reclamacao"].str.split().str.len().astype(int)
    colunas = ["ID", "Data", "Mes", "Semana", "DiaSemana", "Cidade", "Estado", "titulo",
               "Texto_Reclamacao", "n_palavras", "_arquivo_origem", "_ingerido_em"]
    return df[colunas]


def gerar(
    prata_path: Path = config.PRATA_PARQUET,
    duckdb_path: Path = config.OURO_DUCKDB,
    parquet_path: Path = config.OURO_PARQUET,
    resumo_execucao: dict | None = None,
) -> dict:
    df = enriquecer(pd.read_parquet(prata_path))
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)

    temporario = duckdb_path.with_suffix(".tmp.duckdb")
    temporario.unlink(missing_ok=True)
    if duckdb_path.exists():
        shutil.copy(duckdb_path, temporario)  # preserva o histórico de execuções

    with duckdb.connect(str(temporario)) as con:
        con.register("df_reclamacoes", df)
        con.execute("CREATE OR REPLACE TABLE reclamacoes AS SELECT * FROM df_reclamacoes")
        con.execute("""
            CREATE OR REPLACE TABLE reclamacoes_diarias AS
            WITH dias AS (
                SELECT CAST(range AS DATE) AS Data
                FROM range((SELECT min(Data) FROM reclamacoes), (SELECT max(Data) FROM reclamacoes) + INTERVAL 1 DAY, INTERVAL 1 DAY)
            ),
            ufs AS (SELECT DISTINCT Estado FROM reclamacoes)
            SELECT d.Data, u.Estado, count(r.ID) AS quantidade
            FROM dias d CROSS JOIN ufs u
            LEFT JOIN reclamacoes r ON CAST(r.Data AS DATE) = d.Data AND r.Estado = u.Estado
            GROUP BY ALL
            ORDER BY d.Data, u.Estado
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS execucoes (
                executado_em TIMESTAMP, linhas INTEGER, data_min DATE, data_max DATE, resumo JSON
            )
        """)
        resumo = con.execute(
            "SELECT count(*), min(Data)::DATE, max(Data)::DATE FROM reclamacoes"
        ).fetchone()
        con.execute(
            "INSERT INTO execucoes VALUES (?, ?, ?, ?, ?)",
            [datetime.now(timezone.utc).replace(tzinfo=None), resumo[0], resumo[1], resumo[2],
             json.dumps(resumo_execucao or {}, ensure_ascii=False, default=str)],
        )
    temporario.replace(duckdb_path)

    tmp_parquet = parquet_path.with_suffix(".tmp")
    df.to_parquet(tmp_parquet, index=False)
    tmp_parquet.replace(parquet_path)

    log.info("Ouro: %d reclamações de %s a %s", resumo[0], resumo[1], resumo[2])
    return {"linhas": resumo[0], "data_min": str(resumo[1]), "data_max": str(resumo[2])}


def ler_reclamacoes(parquet_path: Path = config.OURO_PARQUET) -> pd.DataFrame:
    """Tabela ouro para o app/agente. Lê o Parquet para não disputar o lock do DuckDB com o pipeline."""
    df = pd.read_parquet(parquet_path)
    df["ID"] = df["ID"].astype(str)
    df["Data"] = pd.to_datetime(df["Data"])
    return df
