import pandas as pd
import pytest

import config


@pytest.fixture(autouse=True)
def dados_isolados(tmp_path, monkeypatch):
    """Cada teste usa pastas temporárias: nenhum teste toca em data/ de verdade."""
    for nome, sub in [
        ("DATA_DIR", ""), ("LANDING_DIR", "landing"), ("BRUTO_DIR", "bruto"), ("BRONZE_DIR", "bronze"), ("PRATA_DIR", "prata"),
        ("QUARENTENA_DIR", "quarentena"), ("QUALIDADE_DIR", "qualidade"), ("OURO_DIR", "ouro"),
        ("INDICE_DIR", "indice"), ("LOGS_DIR", "logs"), ("AVALIACAO_DIR", "avaliacao"),
    ]:
        monkeypatch.setattr(config, nome, tmp_path / sub)
    monkeypatch.setattr(config, "MANIFESTO_BRONZE", tmp_path / "bronze" / "_manifesto.json")
    monkeypatch.setattr(config, "PRATA_PARQUET", tmp_path / "prata" / "reclamacoes.parquet")
    monkeypatch.setattr(config, "OURO_DUCKDB", tmp_path / "ouro" / "reclamacoes.duckdb")
    monkeypatch.setattr(config, "OURO_PARQUET", tmp_path / "ouro" / "reclamacoes.parquet")
    return tmp_path


TEXTO = "Texto longo o bastante para passar na validação de qualidade."


def linha(id, data, cidade="São Paulo", estado="SP", titulo="Cobrança indevida", texto=TEXTO):
    return {"titulo": titulo, "ID": str(id), "Data": data, "Cidade": cidade, "Estado": estado,
            "Texto_Reclamacao": texto}


@pytest.fixture
def df_ouro():
    """Tabela no formato da camada ouro, com casos que exercitam empates e períodos vazios."""
    from pipeline.ouro import enriquecer

    linhas = [
        linha(1, "2026-09-01", "São Paulo", "SP", texto="Pedi carro reserva e não recebi. " + TEXTO),
        linha(2, "2026-09-01", "Campinas", "SP"),
        linha(3, "2026-09-02", "Belo Horizonte", "MG"),
        linha(4, "2026-09-03", "Contagem", "MG"),
        linha(5, "2026-09-10", "Rio de Janeiro", "RJ", texto="Carro Reserva atrasou. " + TEXTO),
        linha(6, "2026-09-10", "Curitiba", "PR"),
        # 04 a 09/09 sem reclamações: precisam aparecer com 0 nos agrupamentos de tempo.
    ]
    df = pd.DataFrame(linhas).assign(_arquivo_origem="teste.csv", _ingerido_em="2026-09-11T00:00:00+00:00")
    return enriquecer(df)
