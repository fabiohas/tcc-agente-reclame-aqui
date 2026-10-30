# Agente Reclame Aqui

Pipeline de dados e agente analítico para reclamações de clientes da Localiza publicadas no Reclame Aqui. Trabalho de conclusão da pós-graduação em Engenharia de Dados.

- **Pipeline** (Airflow): coleta → bronze → prata (qualidade + anonimização) → ouro (DuckDB).
- **Agente** (Streamlit + Claude): responde em linguagem natural perguntas como "quais os 3 estados com mais reclamações?" ou "quantas reclamações tratam de boleto indevido em setembro?". Os números vêm sempre de código determinístico, nunca de estimativa do modelo.

Documentação:

- [Documentação do projeto](docs/documentacao.md): problema, fontes de dados, arquitetura, stack, decisões técnicas, dicionário de dados, escopo, riscos e prova de acesso à fonte.

## Requisitos

- Python 3.12, ou Docker para rodar tudo em containers
- Uma chave da API da Anthropic (`ANTHROPIC_API_KEY`)

```bash
cp .env.exemplo .env   # e coloque sua ANTHROPIC_API_KEY
```

As duas opções abaixo partem desse `.env`.

## Opção 1: Docker (pipeline + Airflow + app)

```bash
echo "AIRFLOW_UID=$(id -u)" >> .env     # só no Linux: permite gravar em data/
docker compose up --build
```

Esse comando roda todas as etapas, sem nenhum passo manual:

1. **`pipeline`** roda uma vez e termina: coleta as reclamações mais recentes do Reclame Aqui e gera as camadas bronze, prata e ouro. Se a coleta falhar (site fora do ar ou bloqueio), segue com os CSVs versionados em `data/landing/`.
2. **Airflow** (http://localhost:8080) sobe em seguida, com o DAG `reclamacoes_reclame_aqui` já ativo para as execuções diárias.
3. **App** (http://localhost:8501) sobe com a camada ouro pronta.

Na primeira vez, o build das imagens leva alguns minutos, e a coleta leva cerca de 1 minuto por página da lista (`RA_MAX_PAGINAS`, padrão 3). Para subir mais rápido sem coletar, coloque `RA_COLETA_ATIVA=false` no `.env`.

## Opção 2: local, sem Docker

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pipeline.executar --sem-coleta   # landing -> bronze -> prata -> ouro
.venv/bin/streamlit run app.py
```

Sem `--sem-coleta`, o pipeline coleta antes as reclamações respondidas mais recentes do Reclame Aqui com o [reclameaqui_scraper.py](reclameaqui_scraper.py) (Selenium; precisa do Google Chrome ou do Chromium instalado). A empresa e o número de páginas vêm de `RA_EMPRESA` e `RA_MAX_PAGINAS` (ver `.env.exemplo`). Se a coleta falhar, o pipeline avisa e segue com os arquivos da landing.

O coletor também roda sozinho. O CSV vai para `data/landing/` e o JSON bruto para `data/bruto/`:

```bash
.venv/bin/python reclameaqui_scraper.py --empresa localiza-meoo --paginas 2   # --visivel abre o Chrome com janela
```

### Adicionar dados novos à mão

Coloque um CSV com as colunas `titulo, ID, Data, Cidade, Estado, Texto_Reclamacao` em `data/landing/` e rode o pipeline. Só os arquivos novos são processados, e IDs repetidos ficam com a versão mais recente.

## Testes

```bash
.venv/bin/python -m pytest
```

Os testes cobrem anonimização, as camadas do pipeline (incremental, deduplicação, quarentena, reprovação de lote), o coletor (com páginas simuladas), as ferramentas do agente, o loop do agente (com cliente simulado) e os corretores da avaliação. Nenhum teste chama a API ou acessa o site.

## Avaliação do agente

```bash
.venv/bin/python -m avaliacao.rodar --listar           # mostra os 16 casos
.venv/bin/python -m avaliacao.rodar                    # roda todos (~US$ 0,70)
.venv/bin/python -m avaliacao.rodar --casos total pct_sp
.venv/bin/python -m avaliacao.rodar --repeticoes 3     # mede a variação entre execuções
```

O relatório fica em `data/avaliacao/<execução>/relatorio.md`, com a taxa de acerto geral e por categoria, o custo e o tempo de cada pergunta. O gabarito é calculado direto dos dados, então continua válido quando chegam reclamações novas.

## Estrutura

```
config.py              configuração central (variáveis de ambiente; ver .env.exemplo)
reclameaqui_scraper.py coletor oficial do Reclame Aqui (Selenium); chamado por pipeline/coleta.py
pipeline/              coleta, bronze, prata, qualidade, anonimização, ouro, executar
dags/                  DAG do Airflow (chama as funções de pipeline/)
dados.py               leitura da camada ouro + índice semântico (embeddings locais)
ferramentas.py         ferramentas do agente (contagens, contagem por critério, busca, leitura)
agente.py              loop do agente com o Claude
telemetria.py          log de tokens e custo de cada chamada à API
app.py                 interface de chat (Streamlit)
avaliacao/             casos de teste, corretores e execução da avaliação
tests/                 testes automatizados (pytest)
docker/                Dockerfiles do app e do Airflow
docs/                  documentação do projeto (documentacao.md)
data/                  camadas de dados, logs e resultados (gerado; só a landing é versionada)
```

## Ferramentas do agente

| Ferramenta              | Uso                                                                                                                                                                                          |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `contar_reclamacoes`  | Totais, rankings, percentuais e estatísticas (média, mediana, mín., máx.) por Estado, Cidade, Data, Semana, Mês ou dia da semana, com filtros. Períodos sem reclamação contam como 0 |
| `contar_por_criterio` | Conta reclamações sobre um assunto em linguagem natural. O Claude Haiku 5.5 lê cada reclamação e decide, com justificativa; as decisões ficam em cache                                 |
| `buscar_reclamacoes`  | Busca semântica para exemplos e resumos                                                                                                                                                     |
| `obter_reclamacao`    | Texto completo pelo ID                                                                                                                                                                       |
| Execução de código   | Cálculos que as outras não cobrem. Os dados da ouro vão para o ambiente isolado da Anthropic (expiram em 7 dias). Desligue com `USAR_EXECUCAO_CODIGO=false`                             |

## Custos e privacidade

- Cada pergunta custa em média US$ 0,03 a 0,05. O custo estimado aparece abaixo de cada resposta e na barra lateral, e fica registrado em `data/logs/`.
- Placas, CPFs, telefones, e-mails e contratos são mascarados na camada prata, antes de chegar ao agente ou à API. A bronze guarda os dados originais e deve ter o acesso restrito.
