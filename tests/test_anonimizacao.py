import pytest

from pipeline.anonimizacao import anonimizar, contar_ocorrencias


@pytest.mark.parametrize("original, esperado", [
    ("Placa TZI3D48 com defeito", "Placa [PLACA] com defeito"),
    ("veículo placa Pzb7d59, modelo", "veículo placa [PLACA], modelo"),
    ("placa antiga ABC-1234", "placa antiga [PLACA]"),
    ("contrato GVM9503/25 ativo", "contrato [CONTRATO] ativo"),
    ("meu CPF é 123.456.789-09", "meu CPF é [CPF]"),
    ("ligue (31) 99999-8888 ou 31 3333-4444", "ligue [TELEFONE] ou [TELEFONE]"),
    ("escreva para fulano.silva@gmail.com", "escreva para [EMAIL]"),
    ("CNPJ 12.345.678/0001-90", "CNPJ [CNPJ]"),
])
def test_mascara_dados_pessoais(original, esperado):
    assert anonimizar(original) == esperado


@pytest.mark.parametrize("texto", [
    "protocolo consumidor.gov.br (2026.08/00016206828)",  # 11 dígitos soltos não são CPF
    "cobrança de R$ 11.083,28 em 21/07/2026",
    "protocolo 13019754 aberto",
    "Hyundai HB20 e Renault Kwid",
])
def test_nao_mascara_o_que_nao_e_dado_pessoal(texto):
    assert anonimizar(texto) == texto


def test_conta_ocorrencias_por_tipo():
    assert contar_ocorrencias("placas TZI3D48 e TYW4J64, contrato GVB3932/25") == {"[PLACA]": 2, "[CONTRATO]": 1}


def test_valores_nao_texto_passam_direto():
    assert anonimizar(None) is None
