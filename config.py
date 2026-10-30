"""Configuração central do projeto. Tudo pode ser sobrescrito por variáveis de ambiente (ou pelo .env)."""

import logging
import os
from pathlib import Path

RAIZ = Path(__file__).parent


def _caminho(var: str, padrao: Path) -> Path:
    return Path(os.getenv(var, str(padrao)))


def _bool(var: str, padrao: bool) -> bool:
    return os.getenv(var, str(padrao)).strip().lower() in {"1", "true", "sim", "yes"}


# ---------- camadas de dados ----------
DATA_DIR = _caminho("DATA_DIR", RAIZ / "data")
LANDING_DIR = DATA_DIR / "landing"          # arquivos de entrada (CSV exportado ou gerado pelo coletor)
BRUTO_DIR = DATA_DIR / "bruto"              # JSON bruto de cada coleta (texto completo das páginas)
BRONZE_DIR = DATA_DIR / "bronze"            # cópia fiel da entrada, em Parquet, com metadados de ingestão
PRATA_DIR = DATA_DIR / "prata"              # dados limpos, validados, deduplicados e anonimizados
QUARENTENA_DIR = DATA_DIR / "quarentena"    # linhas reprovadas na validação, com o motivo
QUALIDADE_DIR = DATA_DIR / "qualidade"      # relatório de qualidade de cada execução
OURO_DIR = DATA_DIR / "ouro"                # tabelas prontas para consumo (DuckDB + Parquet)
INDICE_DIR = DATA_DIR / "indice"            # embeddings da busca semântica e cache de classificações
LOGS_DIR = DATA_DIR / "logs"                # log das chamadas à API (tokens e custo)
AVALIACAO_DIR = DATA_DIR / "avaliacao"      # resultados das avaliações do agente

MANIFESTO_BRONZE = BRONZE_DIR / "_manifesto.json"
PRATA_PARQUET = PRATA_DIR / "reclamacoes.parquet"
OURO_DUCKDB = OURO_DIR / "reclamacoes.duckdb"
OURO_PARQUET = OURO_DIR / "reclamacoes.parquet"

# ---------- qualidade ----------
# Acima deste percentual de linhas em quarentena a execução falha (sinal de problema na fonte).
LIMITE_QUARENTENA_PCT = float(os.getenv("LIMITE_QUARENTENA_PCT", "10"))

# ---------- coleta (Reclame Aqui, via reclameaqui_scraper.py) ----------
RA_EMPRESA = os.getenv("RA_EMPRESA", "localiza-meoo")   # slug da empresa na URL do site
RA_MAX_PAGINAS = int(os.getenv("RA_MAX_PAGINAS", "20"))  # cada página da lista tem ~5 reclamações
RA_COLETA_ATIVA = _bool("RA_COLETA_ATIVA", True)

# ---------- modelos ----------
MODELO_AGENTE = os.getenv("MODELO_AGENTE", "claude-opus-5-5")
MODELO_CLASSIFICADOR = os.getenv("MODELO_CLASSIFICADOR", "claude-haiku-5-5")
MODELO_JUIZ = os.getenv("MODELO_JUIZ", "claude-opus-5-5")
MODELO_EMBEDDINGS = os.getenv(
    "MODELO_EMBEDDINGS", "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
)
USAR_EXECUCAO_CODIGO = _bool("USAR_EXECUCAO_CODIGO", True)

# Preço por milhão de tokens (US$): entrada, saída, leitura de cache, escrita de cache (5 min).
# Valores de referência de out/2026; confira em https://platform.claude.com/docs/en/about-claude/pricing
PRECOS_POR_MILHAO = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
}

# ---------- logging ----------
NIVEL_LOG = os.getenv("NIVEL_LOG", "INFO")


def configurar_logging() -> None:
    logging.basicConfig(
        level=NIVEL_LOG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
