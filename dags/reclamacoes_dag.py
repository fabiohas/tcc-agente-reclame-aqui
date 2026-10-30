"""DAG diário: coleta -> bronze -> prata (qualidade + anonimização) -> ouro.

Cada tarefa chama uma função do pacote pipeline/; o mesmo fluxo roda sem Airflow com
`python -m pipeline.executar`. O índice de embeddings não entra aqui: ele é gerado pelo app
quando a camada ouro muda, para manter a imagem do Airflow sem torch.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow.sdk import dag, task

# O código do projeto fica montado um nível acima da pasta dags/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dag(
    dag_id="reclamacoes_reclame_aqui",
    description="Coleta e prepara as reclamações para o agente",
    schedule="0 6 * * *",
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,  # as camadas são reescritas a cada execução
    is_paused_upon_creation=False,  # já começa ativo: ninguém precisa ligar o DAG na interface
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["tcc", "reclamacoes"],
)
def reclamacoes_reclame_aqui():
    @task
    def coletar() -> dict:
        """Roda o reclameaqui_scraper.py. Nunca falha: um erro vira status e o pipeline segue com a landing."""
        from pipeline import coleta
        from pipeline.executar import ids_conhecidos

        return coleta.executar_coleta(ids_conhecidos())

    @task
    def ingerir_bronze(resultado_coleta: dict) -> dict:
        from pipeline import bronze

        return {"coleta": resultado_coleta, **bronze.ingerir()}

    @task
    def gerar_prata(resumo_bronze: dict) -> dict:
        """Falha (e para o DAG) se o lote não passar nas regras de qualidade."""
        from pipeline import prata

        return prata.gerar()

    @task
    def gerar_ouro(resumo_bronze: dict, relatorio_prata: dict) -> dict:
        from pipeline import ouro

        return ouro.gerar(resumo_execucao={"bronze": resumo_bronze, "prata": relatorio_prata})

    resumo_bronze = ingerir_bronze(coletar())
    gerar_ouro(resumo_bronze, gerar_prata(resumo_bronze))


reclamacoes_reclame_aqui()
