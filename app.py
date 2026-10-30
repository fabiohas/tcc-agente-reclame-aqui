"""Interface de chat em Streamlit. Rode com: streamlit run app.py"""

import json
from datetime import datetime

import anthropic
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import config  # noqa: E402
from agente import Agente, enviar_csv  # noqa: E402
from dados import IndiceSemantico, carregar_dataframe  # noqa: E402
from ferramentas import Ferramentas  # noqa: E402

config.configurar_logging()
st.set_page_config(page_title="Agente Reclame Aqui", page_icon="💬")
st.title("Agente de reclamações")
st.caption("Pergunte sobre as reclamações da Localiza no Reclame Aqui.")

if not config.OURO_PARQUET.exists():
    st.error("A camada ouro ainda não existe. Rode o pipeline: `python -m pipeline.executar --sem-coleta`.")
    st.stop()

# A versão dos dados é a data de modificação da tabela ouro: quando o pipeline roda, tudo recarrega.
versao_dados = config.OURO_PARQUET.stat().st_mtime


@st.cache_resource(show_spinner="Carregando dados e índice semântico…", max_entries=1)
def carregar_ferramentas(versao: float) -> tuple:
    df = carregar_dataframe()
    return df, Ferramentas(df, IndiceSemantico(df))


@st.cache_resource(show_spinner="Enviando os dados para o ambiente de execução de código…", max_entries=1)
def arquivo_csv_na_api(versao: float) -> str | None:
    """file_id da tabela ouro na Files API. None = execução de código desligada."""
    if not config.USAR_EXECUCAO_CODIGO:
        return None
    try:
        return enviar_csv(carregar_ferramentas(versao)[0])
    except anthropic.APIError:
        return None


df, ferramentas = carregar_ferramentas(versao_dados)
arquivo_csv_id = arquivo_csv_na_api(versao_dados)


def novo_agente() -> Agente:
    return Agente(df, ferramentas, arquivo_csv_id)


if "agente" not in st.session_state or st.session_state.get("versao_dados") != versao_dados:
    st.session_state.agente = novo_agente()
    st.session_state.versao_dados = versao_dados
    st.session_state.chat = []  # [(papel, texto, chamadas_de_ferramenta, uso)]
    st.session_state.custo_conversa = 0.0

with st.sidebar:
    st.metric("Reclamações", len(df))
    st.caption(
        f"Período: {df['Data'].min():%d/%m/%Y} a {df['Data'].max():%d/%m/%Y}  \n"
        f"Dados atualizados em {datetime.fromtimestamp(versao_dados):%d/%m/%Y %H:%M}"
    )
    st.metric("Custo estimado da conversa", f"US$ {st.session_state.custo_conversa:.4f}")
    if arquivo_csv_id is None:
        st.caption("Execução de código desligada.")
    if st.button("Nova conversa"):
        st.session_state.agente = novo_agente()
        st.session_state.chat = []
        st.session_state.custo_conversa = 0.0
        st.rerun()
    st.markdown("**Exemplos**")
    st.markdown(
        "- Quais os top 3 estados com mais reclamações?\n"
        "- Quantas reclamações por semana? Qual a média por dia?\n"
        "- Quantas reclamações tratam de boleto indevido nas últimas 3 semanas dos dados?\n"
        "- Quais reclamações falam de cobrança depois de devolver o carro?\n"
        "- Qual o tamanho médio dos textos das reclamações por estado?"
    )


def mostrar_detalhes(chamadas, uso):
    if chamadas:
        with st.expander(f"Ferramentas usadas ({len(chamadas)})"):
            for nome, entrada in chamadas:
                st.code(f"{nome}({json.dumps(entrada, ensure_ascii=False)})", language=None)
    if uso:
        st.caption(
            f"{uso['chamadas']} chamadas à API · {uso['input_tokens'] + uso['cache_read_input_tokens']:,} tokens de entrada "
            f"({uso['cache_read_input_tokens']:,} do cache) · {uso['output_tokens']:,} de saída · "
            f"custo estimado US$ {uso['custo_usd']:.4f}".replace(",", ".")
        )


for papel, texto, chamadas, uso in st.session_state.chat:
    with st.chat_message(papel):
        mostrar_detalhes(chamadas, None)
        st.markdown(texto)
        if uso:
            mostrar_detalhes([], uso)

if pergunta := st.chat_input("Faça uma pergunta"):
    st.session_state.chat.append(("user", pergunta, [], None))
    with st.chat_message("user"):
        st.markdown(pergunta)

    with st.chat_message("assistant"):
        chamadas = []
        with st.status("Analisando…") as status:
            def registrar(nome, entrada):
                chamadas.append((nome, entrada))
                status.write(f"`{nome}` {json.dumps(entrada, ensure_ascii=False)}")

            try:
                resposta = st.session_state.agente.perguntar(pergunta, registrar)
                status.update(label="Pronto", state="complete")
            except anthropic.AuthenticationError:
                resposta = "Chave da API inválida. Confira ANTHROPIC_API_KEY no arquivo .env."
                status.update(label="Erro", state="error")
            except anthropic.RateLimitError:
                resposta = "Limite de requisições atingido. Aguarde um pouco e tente de novo."
                status.update(label="Erro", state="error")
            except anthropic.APIConnectionError:
                resposta = "Falha de conexão com a API da Anthropic."
                status.update(label="Erro", state="error")
            except anthropic.APIStatusError as e:
                resposta = f"Erro da API ({e.status_code}): {e.message}"
                status.update(label="Erro", state="error")
        st.markdown(resposta)
        uso = st.session_state.agente.ultimo_uso or None
        if uso:
            mostrar_detalhes([], uso)
            st.session_state.custo_conversa += uso["custo_usd"]
    st.session_state.chat.append(("assistant", resposta, chamadas, uso))
    st.rerun()  # atualiza o custo da conversa na barra lateral
