"""Camada bronze: cópia fiel dos arquivos da landing, em Parquet, com metadados de ingestão.

A carga é incremental: cada arquivo é identificado pelo hash SHA-256 do conteúdo e registrado
num manifesto. Rodar de novo não duplica nada; um arquivo só é reprocessado se o conteúdo mudar.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import config

log = logging.getLogger(__name__)

COLUNAS_ESPERADAS = ["titulo", "ID", "Data", "Cidade", "Estado", "Texto_Reclamacao"]


def _hash(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def carregar_manifesto(caminho: Path = config.MANIFESTO_BRONZE) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def ingerir(
    landing: Path = config.LANDING_DIR,
    bronze: Path = config.BRONZE_DIR,
    manifesto_path: Path = config.MANIFESTO_BRONZE,
) -> dict:
    """Converte para Parquet os CSVs da landing ainda não vistos. Devolve um resumo da execução."""
    manifesto = carregar_manifesto(manifesto_path)
    destino = bronze / "reclamacoes"
    destino.mkdir(parents=True, exist_ok=True)
    novos, ignorados = [], 0

    for arquivo in sorted(landing.glob("*.csv")):
        digest = _hash(arquivo)
        if digest in manifesto:
            ignorados += 1
            continue
        # Tudo como texto: a bronze não interpreta nada, só guarda. Tipos são tratados na prata.
        df = pd.read_csv(arquivo, dtype=str, keep_default_na=False)
        faltando = [c for c in COLUNAS_ESPERADAS if c not in df.columns]
        if faltando:
            raise ValueError(f"{arquivo.name}: colunas ausentes {faltando}")
        ingerido_em = datetime.now(timezone.utc).isoformat(timespec="seconds")
        df = df[COLUNAS_ESPERADAS].assign(
            _arquivo_origem=arquivo.name, _hash_arquivo=digest, _ingerido_em=ingerido_em
        )
        saida = destino / f"{digest[:16]}.parquet"
        df.to_parquet(saida, index=False)
        manifesto[digest] = {
            "arquivo": arquivo.name, "linhas": len(df), "ingerido_em": ingerido_em, "parquet": saida.name,
        }
        novos.append(arquivo.name)
        log.info("Bronze: %s -> %s (%d linhas)", arquivo.name, saida.name, len(df))

    manifesto_path.parent.mkdir(parents=True, exist_ok=True)
    manifesto_path.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"arquivos_novos": novos, "arquivos_ja_ingeridos": ignorados, "total_arquivos": len(manifesto)}


def ler_bronze(bronze: Path = config.BRONZE_DIR) -> pd.DataFrame:
    arquivos = sorted((bronze / "reclamacoes").glob("*.parquet"))
    if not arquivos:
        return pd.DataFrame(columns=COLUNAS_ESPERADAS + ["_arquivo_origem", "_hash_arquivo", "_ingerido_em"])
    return pd.concat([pd.read_parquet(a) for a in arquivos], ignore_index=True)
