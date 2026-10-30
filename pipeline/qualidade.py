"""Regras de qualidade dos dados (Pandera).

Dois níveis:
- linha: cada reclamação precisa passar no esquema; as que falham vão para a quarentena com o motivo;
- lote: se a quarentena passar de LIMITE_QUARENTENA_PCT ou não sobrar nenhuma linha válida,
  a execução falha. Isso indica problema na fonte (layout mudou, arquivo corrompido etc.).
"""

import pandas as pd
import pandera.pandas as pa

UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}


class ErroQualidade(RuntimeError):
    """O lote não atende aos critérios mínimos de qualidade."""


def esquema(hoje: pd.Timestamp) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "ID": pa.Column(checks=pa.Check.str_matches(r"^\d+$", error="ID não numérico")),
            "Data": pa.Column(
                nullable=False,
                checks=[
                    pa.Check.ge(pd.Timestamp("2000-01-01"), error="data anterior a 2000"),
                    pa.Check.le(hoje, error="data no futuro"),
                ],
            ),
            "Estado": pa.Column(checks=pa.Check.isin(sorted(UFS), error="UF inválida")),
            "Cidade": pa.Column(checks=pa.Check.str_length(min_value=2, error="cidade vazia")),
            "titulo": pa.Column(checks=pa.Check.str_length(min_value=5, error="título vazio ou curto")),
            "Texto_Reclamacao": pa.Column(
                checks=pa.Check.str_length(min_value=20, error="texto vazio ou curto")
            ),
        },
        strict=False,
    )


def validar(df: pd.DataFrame, hoje: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separa linhas válidas e quarentena. A quarentena ganha a coluna _motivos."""
    hoje = (hoje or pd.Timestamp.now()).normalize()
    try:
        esquema(hoje).validate(df, lazy=True)
        return df, df.iloc[0:0].assign(_motivos=pd.Series(dtype=str))
    except pa.errors.SchemaErrors as erros:
        casos = erros.failure_cases.dropna(subset=["index"])
        motivos = {}
        for _, caso in casos.iterrows():
            texto = caso["check"]
            # Dados ausentes aparecem como "not_nullable"; deixamos o motivo legível.
            if texto == "not_nullable":
                texto = "valor ausente ou inválido"
            motivos.setdefault(caso["index"], set()).add(f"{caso['column']}: {texto}")
        ruins = df.index.isin(list(motivos))
        quarentena = df[ruins].copy()
        quarentena["_motivos"] = [" | ".join(sorted(motivos[i])) for i in quarentena.index]
        return df[~ruins], quarentena


def checar_lote(total: int, validas: int, limite_pct: float) -> dict:
    pct = round(100 * (total - validas) / total, 2) if total else 0.0
    resultado = {"total": total, "validas": validas, "quarentena": total - validas, "quarentena_pct": pct}
    if total and validas == 0:
        raise ErroQualidade(f"Nenhuma linha válida no lote: {resultado}")
    if pct > limite_pct:
        raise ErroQualidade(f"Quarentena de {pct}% passou do limite de {limite_pct}%: {resultado}")
    return resultado
