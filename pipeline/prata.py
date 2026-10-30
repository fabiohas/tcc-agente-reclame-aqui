"""Camada prata: dados limpos, tipados, deduplicados, validados e anonimizados."""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import config
from pipeline import anonimizacao, qualidade
from pipeline.bronze import ler_bronze

log = logging.getLogger(__name__)

COLUNAS_TEXTO = ["titulo", "Cidade", "Estado", "Texto_Reclamacao"]


def padronizar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ID"] = df["ID"].astype(str).str.strip()
    for col in COLUNAS_TEXTO:
        df[col] = df[col].astype(str).str.strip()
    df["Cidade"] = df["Cidade"].str.replace(r"\s+", " ", regex=True)
    df["Estado"] = df["Estado"].str.upper()
    # Data inválida vira NaT e é barrada na validação (vai para a quarentena).
    df["Data"] = pd.to_datetime(df["Data"].astype(str).str.strip().str[:10], format="%Y-%m-%d", errors="coerce")
    return df


def deduplicar(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Uma linha por ID: vale a versão ingerida por último (ex.: reclamação editada no site)."""
    ordenado = df.sort_values("_ingerido_em", kind="stable")
    unicos = ordenado.drop_duplicates("ID", keep="last")
    return unicos.sort_values(["Data", "ID"], ascending=[False, True]).reset_index(drop=True), len(df) - len(unicos)


def gerar(
    bronze: Path = config.BRONZE_DIR,
    prata_path: Path = config.PRATA_PARQUET,
    quarentena_dir: Path = config.QUARENTENA_DIR,
    qualidade_dir: Path = config.QUALIDADE_DIR,
    limite_quarentena_pct: float = config.LIMITE_QUARENTENA_PCT,
    hoje: pd.Timestamp | None = None,
) -> dict:
    execucao = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bruto = ler_bronze(bronze)
    df, duplicadas = deduplicar(padronizar(bruto))
    validas, quarentena = qualidade.validar(df, hoje)

    # Anonimização depois da validação: as regras olham o texto original.
    pii = Counter()
    for col in ["titulo", "Texto_Reclamacao"]:
        for texto in validas[col]:
            pii.update(anonimizacao.contar_ocorrencias(texto))
    validas = validas.assign(**{c: validas[c].map(anonimizacao.anonimizar) for c in ["titulo", "Texto_Reclamacao"]})
    quarentena = quarentena.assign(
        **{c: quarentena[c].map(anonimizacao.anonimizar) for c in ["titulo", "Texto_Reclamacao"]}
    )

    relatorio = {
        "execucao": execucao,
        "linhas_bronze": len(bruto),
        "duplicadas_removidas": duplicadas,
        "motivos_quarentena": dict(Counter(
            m for ms in quarentena["_motivos"] for m in ms.split(" | ")
        )) if len(quarentena) else {},
        "dados_pessoais_mascarados": dict(pii),
    }
    try:
        relatorio["lote"] = qualidade.checar_lote(len(df), len(validas), limite_quarentena_pct)
        relatorio["status"] = "aprovado"
    except qualidade.ErroQualidade as e:
        relatorio["status"] = "reprovado"
        relatorio["erro"] = str(e)
        raise
    finally:
        # O relatório é gravado mesmo quando o lote é reprovado, para diagnóstico.
        qualidade_dir.mkdir(parents=True, exist_ok=True)
        (qualidade_dir / f"relatorio_{execucao}.json").write_text(
            json.dumps(relatorio, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        if len(quarentena):
            quarentena_dir.mkdir(parents=True, exist_ok=True)
            quarentena.to_parquet(quarentena_dir / f"quarentena_{execucao}.parquet", index=False)

    # Só substitui a prata se o lote foi aprovado (a versão anterior continua valendo se falhar).
    prata_path.parent.mkdir(parents=True, exist_ok=True)
    temporario = prata_path.with_suffix(".tmp")
    validas.to_parquet(temporario, index=False)
    temporario.replace(prata_path)
    log.info("Prata: %d válidas, %d em quarentena, %d duplicadas", len(validas), len(quarentena), duplicadas)
    return relatorio
