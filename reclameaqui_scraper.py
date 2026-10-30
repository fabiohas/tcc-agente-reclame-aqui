"""
Coletor oficial de reclamações do Reclame Aqui.

Coleta as reclamações respondidas de uma empresa, extrai o texto de cada
página e gera dois arquivos:
  - CSV limpo (titulo, ID, Data, Cidade, Estado, Texto_Reclamacao) em data/landing/,
    a pasta de entrada do pipeline (bronze -> prata -> ouro)
  - JSON bruto (título, link e texto completo da página) em data/bruto/, para auditoria

O pipeline chama este módulo pela tarefa de coleta (pipeline/coleta.py), mas ele
também roda sozinho pela linha de comando.

Dependências:
    pip install selenium pandas

Requer o Google Chrome ou o Chromium instalado. O chromedriver é baixado
automaticamente pelo Selenium Manager. Em Docker/Linux, dá para apontar
binários específicos com as variáveis CHROME_BINARY e CHROMEDRIVER_PATH
(ex.: /usr/bin/chromium e /usr/bin/chromedriver).

Uso pela linha de comando:
    python reclameaqui_scraper.py --empresa localiza-meoo --paginas 2

Uso como biblioteca:
    from reclameaqui_scraper import executar
    df = executar(empresa="localiza-meoo", max_paginas=2)
"""
import argparse
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Mesmas pastas do config.py (o DATA_DIR também pode vir do ambiente).
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent / "data"))
PASTA_CSV = DATA_DIR / "landing"
PASTA_JSON = DATA_DIR / "bruto"

COLUNAS = ["titulo", "ID", "Data", "Cidade", "Estado", "Texto_Reclamacao"]

URL_LISTA = "https://www.reclameaqui.com.br/empresa/{empresa}/lista-reclamacoes/?status=ANSWERED"
XPATH_RECLAMACOES = "//*[starts-with(@id, 'complaint-title-')]/a"
XPATH_BOTAO_PROXIMO = '//*[@id="main-content"]/div[2]/div[2]/section/div[2]/div[2]/astro-island/div/nav//ul/li[last()]/button'
XPATH_TEXTO_RECLAMACAO = "//body"

TOKENS_ANTIBOT = (
    "performing security verification",
    "this website uses a security service",
    "cloudflare",
)
MSG_FALHA = "Erro: não foi possível extrair o texto (timeout ou bloqueio anti-bot)."


# ========================================================
# NAVEGADOR
# ========================================================

def iniciar_driver(headless=True):
    """Inicializa o Chrome. Usa CHROME_BINARY/CHROMEDRIVER_PATH se definidos."""
    options = Options()
    binario = os.getenv("CHROME_BINARY")
    if binario:
        options.binary_location = binario
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    caminho_driver = os.getenv("CHROMEDRIVER_PATH")
    service = Service(executable_path=caminho_driver) if caminho_driver else Service()
    driver = webdriver.Chrome(service=service, options=options)
    # Sem limite, uma página travada segura o processo até o timeout interno (120 s).
    driver.set_page_load_timeout(60)
    return driver


# ========================================================
# COLETA
# ========================================================

def coletar_links(driver, empresa, max_paginas=1):
    """Navega pela paginação e coleta títulos e links das reclamações."""
    print("--- ETAPA 1: COLETANDO LINKS ---")
    driver.get(URL_LISTA.format(empresa=empresa))
    time.sleep(5)

    reclamacoes = []
    for pagina in range(1, max_paginas + 1):
        print(f"Lendo lista - página {pagina}...")
        try:
            elementos = WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.XPATH, XPATH_RECLAMACOES))
            )
            for el in elementos:
                titulo = el.text.strip()
                if titulo:
                    reclamacoes.append({"titulo": titulo, "link": el.get_attribute("href"), "texto": ""})

            if pagina == max_paginas:
                break

            botao_proximo = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, XPATH_BOTAO_PROXIMO))
            )
            if botao_proximo.get_attribute("disabled"):
                break

            driver.execute_script("arguments[0].click();", botao_proximo)
            time.sleep(4)
        except Exception as e:
            print(f"Erro na paginação: {e}")
            break

    print(f"Etapa 1 concluída: {len(reclamacoes)} links coletados.\n")
    return reclamacoes


def extrair_textos(reclamacoes, headless=True):
    """Abre cada link em um navegador novo e extrai o texto da página."""
    print("--- ETAPA 2: EXTRAINDO TEXTOS ---")

    for i, reclamacao in enumerate(reclamacoes):
        print(f"Acessando link {i + 1} de {len(reclamacoes)}...")
        texto_extraido = ""

        page_driver = iniciar_driver(headless=headless)
        try:
            for tentativa in range(3):
                try:
                    page_driver.get(reclamacao["link"])
                    time.sleep(4 + tentativa)
                    elemento = WebDriverWait(page_driver, 20).until(
                        EC.presence_of_element_located((By.XPATH, XPATH_TEXTO_RECLAMACAO))
                    )
                    texto_extraido = elemento.text.strip()

                    if any(token in texto_extraido.lower() for token in TOKENS_ANTIBOT):
                        print(f"  -> Desafio anti-bot na tentativa {tentativa + 1}. Tentando novamente...")
                        texto_extraido = ""
                        continue

                    if not texto_extraido:
                        raise TimeoutException("Corpo da página vazio")
                    break
                except TimeoutException:
                    print(f"  -> Timeout na tentativa {tentativa + 1}.")
                except WebDriverException as e:
                    print(f"  -> WebDriver falhou na tentativa {tentativa + 1}: {type(e).__name__}")
        finally:
            page_driver.quit()

        if texto_extraido:
            reclamacao["texto"] = texto_extraido
            print("  -> Texto extraído com sucesso!")
        else:
            print(f"  -> ALERTA: falha ao extrair a reclamação {i + 1}.")
            reclamacao["texto"] = MSG_FALHA

        time.sleep(random.uniform(4.0, 7.5))

    return reclamacoes


# ========================================================
# LIMPEZA
# ========================================================

def limpar_texto_reclamacao(texto):
    if not isinstance(texto, str):
        return texto

    texto = texto.replace("\r", "\n")
    texto = re.sub(r"\s*\|\s*", "\n", texto)

    # O site já exibiu "ID: 123" e hoje exibe "ID 123"
    partes = re.split(r"ID:?\s*\d+\s*\n", texto, maxsplit=1)
    if len(partes) > 1:
        texto = partes[1]

    # Depois do ID vem um cabeçalho (status, título, empresa, cidade, data).
    # O relato começa em "O que aconteceu"; se não houver, logo após a data/hora.
    partes = re.split(r"(?i)O que aconteceu\s*\n", texto, maxsplit=1)
    if len(partes) > 1:
        texto = partes[1]
    else:
        partes = re.split(r"\d{2}/\d{2}/\d{4}\s*às\s*\d{2}:\d{2}\s*\n", texto, maxsplit=1)
        if len(partes) > 1:
            texto = partes[1]

    texto = re.split(r"(?i)Sua reação ajuda a comunidade", texto, maxsplit=1)[0]

    if "Resposta da empresa" in texto:
        texto = texto.split("Resposta da empresa", 1)[0]

    texto = re.split(r"(?i)Reclamações parecidas", texto, maxsplit=1)[0]

    texto = texto.replace("RA Ads", "")
    texto = re.sub(
        r"(?im)^\s*(Publicidade|Control your privacy|Our website uses cookies to improve navigation\.?"
        r"|Privacy Policy - Terms of use|Customize|Reject|Accept|Entrar|Criar conta"
        r"|Previous slide|Next slide|Compartilhe)\s*$",
        "",
        texto,
    )
    texto = texto.replace("Previous slide", "").replace("Next slide", "").replace("Compartilhe", "").strip()
    texto = re.sub(r"\n{2,}", "\n", texto)
    return texto.strip(" \n|")


def processar_dados(reclamacoes):
    """Transforma a lista bruta em um DataFrame com colunas estruturadas."""
    print("\n--- ETAPA 3: PROCESSAMENTO E LIMPEZA ---")
    df = pd.DataFrame(reclamacoes)
    if df.empty:
        return pd.DataFrame(columns=COLUNAS)

    df = df.dropna(subset=["texto"]).copy()
    df["texto"] = df["texto"].astype(str).str.strip()
    df = df[df["texto"] != ""]
    df = df[~df["texto"].str.lower().str.contains("|".join(TOKENS_ANTIBOT), na=False)]

    df["ID"] = df["texto"].str.extract(r"ID:?\s*(\d+)", expand=False)
    data_hora = df["texto"].str.extract(r"(\d{2}/\d{2}/\d{4}\s*às\s*\d{2}:\d{2})", expand=False)
    df["Data"] = pd.to_datetime(data_hora, format="%d/%m/%Y às %H:%M", errors="coerce").dt.normalize()
    df = df.dropna(subset=["ID", "Data"]).copy()

    # Pode haver uma linha "data de publicação: ..." entre a cidade e a data
    local = df["texto"].str.extract(
        r"(?P<Cidade>[A-Za-zÀ-ÿ\- ]+?)\s*-\s*(?P<Estado>[A-Z]{2})\n"
        r"(?:data de publicação:[^\n]*\n)?\d{2}/\d{2}/\d{4}"
    )
    df["Cidade"] = local["Cidade"].str.strip()
    df["Estado"] = local["Estado"]

    df["Texto_Reclamacao"] = df["texto"].apply(limpar_texto_reclamacao).fillna("")

    return df[COLUNAS].reset_index(drop=True)


# ========================================================
# ORQUESTRAÇÃO
# ========================================================

def coletar(empresa="localiza-meoo", max_paginas=1, headless=True, ids_conhecidos=None):
    """Coleta e limpa. Devolve (lista bruta, DataFrame limpo sem os IDs já conhecidos).

    A lista só tem reclamações respondidas, e uma reclamação respondida depois aparece
    no meio das antigas. Por isso os IDs conhecidos são pulados, sem encerrar a coleta.
    """
    driver = iniciar_driver(headless=headless)
    try:
        reclamacoes = coletar_links(driver, empresa, max_paginas=max_paginas)
    finally:
        driver.quit()

    if not reclamacoes:
        print("Nenhuma reclamação coletada.")
        return [], pd.DataFrame(columns=COLUNAS)

    reclamacoes = extrair_textos(reclamacoes, headless=headless)
    df = processar_dados(reclamacoes)
    if ids_conhecidos:
        antes = len(df)
        df = df[~df["ID"].astype(str).isin({str(i) for i in ids_conhecidos})].reset_index(drop=True)
        print(f"{antes - len(df)} reclamações já conhecidas foram ignoradas.")
    return reclamacoes, df


def salvar(reclamacoes, df, empresa, pasta_csv=PASTA_CSV, pasta_json=PASTA_JSON):
    """Grava o JSON bruto e, se houver linhas, o CSV. Devolve os caminhos (None se não gravou)."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminhos = {"json": None, "csv": None}

    if reclamacoes:
        pasta_json = Path(pasta_json)
        pasta_json.mkdir(parents=True, exist_ok=True)
        caminhos["json"] = pasta_json / f"reclameaqui_{empresa}_{stamp}.json"
        with open(caminhos["json"], "w", encoding="utf-8") as f:
            json.dump(reclamacoes, f, ensure_ascii=False, indent=4)
        print(f"\nJSON salvo em '{caminhos['json']}'.")

    # Um CSV vazio na landing não acrescenta nada à bronze.
    if not df.empty:
        pasta_csv = Path(pasta_csv)
        pasta_csv.mkdir(parents=True, exist_ok=True)
        caminhos["csv"] = pasta_csv / f"reclameaqui_{empresa}_{stamp}.csv"
        df.to_csv(caminhos["csv"], index=False, encoding="utf-8")
        print(f"CSV salvo em '{caminhos['csv']}' ({len(df)} linhas).")
    else:
        print("Nenhuma reclamação nova: CSV não gravado.")

    return caminhos


def executar(empresa="localiza-meoo", max_paginas=1, pasta_csv=PASTA_CSV, pasta_json=PASTA_JSON,
             headless=True, ids_conhecidos=None):
    """Roda coleta + limpeza e salva CSV e JSON. Retorna o DataFrame."""
    reclamacoes, df = coletar(empresa, max_paginas, headless=headless, ids_conhecidos=ids_conhecidos)
    salvar(reclamacoes, df, empresa, pasta_csv=pasta_csv, pasta_json=pasta_json)
    return df


def main():
    parser = argparse.ArgumentParser(description="Coletor de reclamações do Reclame Aqui.")
    parser.add_argument("--empresa", default="localiza-meoo", help="slug da empresa na URL do Reclame Aqui")
    parser.add_argument("--paginas", type=int, default=1, help="quantidade de páginas da lista a percorrer")
    parser.add_argument("--saida-csv", default=str(PASTA_CSV), help="pasta do CSV (padrão: landing do pipeline)")
    parser.add_argument("--saida-json", default=str(PASTA_JSON), help="pasta do JSON bruto")
    parser.add_argument("--visivel", action="store_true", help="abre o navegador com janela (sem headless)")
    args = parser.parse_args()

    executar(
        empresa=args.empresa,
        max_paginas=args.paginas,
        pasta_csv=args.saida_csv,
        pasta_json=args.saida_json,
        headless=not args.visivel,
    )


if __name__ == "__main__":
    main()
