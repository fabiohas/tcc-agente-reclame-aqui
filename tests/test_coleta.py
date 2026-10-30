import pandas as pd
import pytest

import config
import reclameaqui_scraper as scraper
from pipeline import coleta

# Texto fictício no formato que o site exibe hoje (body inteiro da página).
PAGINA = """Para você
Home
Empresa Teste
ID {id}
Respondida
Carro entregue com defeito
Empresa Teste
cidade: São José dos Campos
São José dos Campos - SP
data de publicação: 28/10/2026
28/10/2026 às 16:36
RA Ads
O que aconteceu
Recebi o carro com a bateria descarregada.
Ninguém resolveu até agora.
Sua reação ajuda a comunidade a saber em quem confiar.
RA Ads
Reclamações parecidas
Outra reclamação
29/10/2026 às 15:44
"""


def bruta(id, texto=None):
    return {"titulo": f"Reclamação {id}", "link": f"https://exemplo/{id}", "texto": texto or PAGINA.format(id=id)}


def test_limpeza_deixa_so_o_relato():
    assert scraper.limpar_texto_reclamacao(PAGINA.format(id=1)) == (
        "Recebi o carro com a bateria descarregada.\nNinguém resolveu até agora."
    )


def test_limpeza_sem_o_que_aconteceu_comeca_apos_a_data():
    texto = PAGINA.format(id=1).replace("O que aconteceu\n", "")
    assert scraper.limpar_texto_reclamacao(texto).startswith("Recebi o carro")


def test_processa_campos_e_descarta_falhas():
    df = scraper.processar_dados([bruta(111), bruta(222, scraper.MSG_FALHA)])
    assert list(df.columns) == scraper.COLUNAS
    assert len(df) == 1
    linha = df.iloc[0]
    assert (linha["ID"], linha["Cidade"], linha["Estado"]) == ("111", "São José dos Campos", "SP")
    assert linha["Data"] == pd.Timestamp("2026-10-28")


def test_coletar_pula_ids_conhecidos_sem_parar(monkeypatch):
    class DriverFalso:
        def quit(self):
            pass

    monkeypatch.setattr(scraper, "iniciar_driver", lambda headless=True: DriverFalso())
    monkeypatch.setattr(scraper, "coletar_links", lambda d, e, max_paginas: [bruta(1), bruta(2), bruta(3)])
    monkeypatch.setattr(scraper, "extrair_textos", lambda r, headless=True: r)

    brutas, df = scraper.coletar("empresa-teste", 1, ids_conhecidos={"2"})
    assert len(brutas) == 3
    assert df["ID"].tolist() == ["1", "3"]


def test_salva_csv_no_formato_da_landing_e_json_no_bruto(tmp_path):
    brutas = [bruta(111)]
    caminhos = scraper.salvar(brutas, scraper.processar_dados(brutas), "teste", tmp_path / "landing", tmp_path / "bruto")
    assert caminhos["csv"].parent == tmp_path / "landing"
    assert caminhos["json"].parent == tmp_path / "bruto"
    # Mesma leitura da bronze: tudo como texto.
    lido = pd.read_csv(caminhos["csv"], dtype=str, keep_default_na=False)
    assert list(lido.columns) == scraper.COLUNAS
    assert lido.loc[0, "Data"] == "2026-10-28"


def test_nao_grava_csv_vazio(tmp_path):
    caminhos = scraper.salvar([bruta(1)], pd.DataFrame(columns=scraper.COLUNAS), "teste", tmp_path / "l", tmp_path / "b")
    assert caminhos["csv"] is None
    assert not (tmp_path / "l").exists()


def test_executar_coleta_desativada(monkeypatch):
    monkeypatch.setattr(config, "RA_COLETA_ATIVA", False)
    assert coleta.executar_coleta() == {"status": "desativada", "novas": 0}


def test_executar_coleta_transforma_erro_em_status(monkeypatch):
    def quebra(**kwargs):
        raise RuntimeError("Chrome não encontrado")

    monkeypatch.setattr(config, "RA_COLETA_ATIVA", True)
    monkeypatch.setattr(scraper, "coletar", quebra)
    resultado = coleta.executar_coleta()
    assert resultado["status"] == "erro"
    assert "Chrome não encontrado" in resultado["motivo"]


def test_executar_coleta_grava_na_landing(monkeypatch, tmp_path):
    brutas = [bruta(111), bruta(222, scraper.MSG_FALHA)]
    monkeypatch.setattr(config, "RA_COLETA_ATIVA", True)
    monkeypatch.setattr(config, "LANDING_DIR", tmp_path / "landing")
    monkeypatch.setattr(config, "BRUTO_DIR", tmp_path / "bruto")
    monkeypatch.setattr(scraper, "coletar", lambda **kw: (brutas, scraper.processar_dados(brutas)))

    resultado = coleta.executar_coleta({"999"})
    assert resultado["status"] == "ok"
    assert (resultado["links"], resultado["falhas"], resultado["novas"]) == (2, 1, 1)
    assert resultado["arquivo"].startswith(str(tmp_path / "landing"))
