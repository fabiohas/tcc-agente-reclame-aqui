"""Roda o pipeline completo sem Airflow: coleta -> bronze -> prata -> ouro.

Uso: python -m pipeline.executar [--sem-coleta]
O DAG do Airflow (dags/reclamacoes_dag.py) chama exatamente estas mesmas funções.
"""

import argparse
import json
import logging

import config
from pipeline import bronze, coleta, ouro, prata

log = logging.getLogger(__name__)


def ids_conhecidos() -> set[str]:
    """IDs já na prata: o coletor para quando chega neles."""
    if not config.PRATA_PARQUET.exists():
        return set()
    import pandas as pd

    return set(pd.read_parquet(config.PRATA_PARQUET, columns=["ID"])["ID"].astype(str))


def executar(com_coleta: bool = True) -> dict:
    resumo = {}
    resumo["coleta"] = coleta.executar_coleta(ids_conhecidos()) if com_coleta else {"status": "pulada"}
    resumo["bronze"] = bronze.ingerir()
    resumo["prata"] = prata.gerar()
    resumo["ouro"] = ouro.gerar(resumo_execucao=resumo)
    return resumo


if __name__ == "__main__":
    config.configurar_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sem-coleta", action="store_true", help="não acessa o Reclame Aqui")
    args = parser.parse_args()
    print(json.dumps(executar(com_coleta=not args.sem_coleta), ensure_ascii=False, indent=2, default=str))
