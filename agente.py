"""Agente que responde perguntas sobre as reclamações usando Claude + ferramentas locais."""

import time
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import anthropic
import pandas as pd

import config
import telemetria
from ferramentas import DEFINICOES, Ferramentas

MODELO = config.MODELO_AGENTE
MAX_ITERACOES = 15
COLUNAS_CSV = ["ID", "Data", "Mes", "Semana", "DiaSemana", "Cidade", "Estado", "titulo",
               "Texto_Reclamacao", "n_palavras"]

# Execução de código no ambiente isolado da Anthropic: reserva para cálculos que as ferramentas não cobrem.
FERRAMENTA_CODIGO = {"type": "code_execution_20260521", "name": "code_execution"}
NOME_CSV_NO_AMBIENTE = "reclamacoes.csv"
VALIDADE_UPLOAD_SEGUNDOS = 7 * 24 * 3600


def enviar_csv(df: pd.DataFrame) -> str:
    """Envia a tabela ouro como CSV à Files API e devolve o file_id (expira sozinho em 7 dias)."""
    conteudo = df[COLUNAS_CSV].assign(Data=df["Data"].dt.strftime("%Y-%m-%d")).to_csv(index=False)
    arquivo = anthropic.Anthropic().files.upload(
        file=(NOME_CSV_NO_AMBIENTE, conteudo.encode("utf-8"), "text/csv"),
        expires_in_seconds=VALIDADE_UPLOAD_SEGUNDOS,
    )
    return arquivo.id


def montar_system_prompt(df: pd.DataFrame, hoje: date, com_codigo: bool) -> str:
    inicio, fim = df["Data"].min().date(), df["Data"].max().date()
    prompt = f"""Você é um analista de reclamações de clientes da Localiza registradas no Reclame Aqui.
Responda em português, de forma direta, sempre com base nos dados obtidos pelas ferramentas.

Hoje é {hoje.isoformat()}.

Sobre o dataset:
- {len(df)} reclamações, de {inicio} a {fim}.
- Colunas: ID, Data, Mes, Semana (segunda-feira da semana), DiaSemana, Cidade, Estado (sigla da UF), titulo, Texto_Reclamacao, n_palavras.
- Dados pessoais (placas, CPF, telefones, e-mails, contratos) foram trocados por marcadores como [PLACA].

Como escolher a ferramenta:
- Totais, rankings, distribuição e evolução no tempo (estado, cidade, dia, semana, mês, dia da semana), percentuais e médias por período: contar_reclamacoes. Informe os números exatos e mencione empate no corte do ranking quando houver.
- Quantas reclamações tratam de um assunto (ex.: "boleto indevido", "demora no reembolso"): contar_por_criterio. Escreva o critério de forma precisa, aplique os filtros de período/local da pergunta e diga que a contagem foi feita por um modelo lendo cada reclamação. Se houver reclamações não classificadas, mencione.
- Exemplos, resumos e "do que falam as reclamações sobre X": buscar_reclamacoes, lendo os trechos antes de concluir. Não conte pelo resultado da busca.
- Menções literais de uma palavra (ex.: "boleto"): contar_reclamacoes com contem_termo. Deixe claro que citar a palavra não garante que a reclamação seja sobre o assunto.
- Para citar exemplos, inclua o ID da reclamação.

Períodos relativos ("últimas 3 semanas", "este mês"): conte a partir de hoje. Se o período passar do fim dos dados ({fim}), diga isso e informe o que os dados cobrem. Em semanas, avise quando a primeira ou a última for parcial."""

    if com_codigo:
        prompt += f"""

Execução de código: o arquivo {NOME_CSV_NO_AMBIENTE} (mesmas colunas) está no diretório $INPUT_DIR do ambiente de execução. Use code_execution só para cálculos que as outras ferramentas não fazem (ex.: tamanho médio dos textos, cruzamentos, correlações, gráficos). Ao usar, diga em uma frase o que o código calculou."""

    prompt += "\n\nSe os dados não permitirem responder, diga isso em vez de supor."
    return prompt


class Agente:
    def __init__(
        self,
        df: pd.DataFrame,
        ferramentas: Ferramentas,
        arquivo_csv_id: str | None = None,
        hoje: date | None = None,
        client: anthropic.Anthropic | None = None,
    ):
        self.client = client or anthropic.Anthropic()
        self.ferramentas = ferramentas
        self.arquivo_csv_id = arquivo_csv_id
        self.system = montar_system_prompt(df, hoje or date.today(), com_codigo=arquivo_csv_id is not None)
        self.conversa_id = uuid.uuid4().hex[:12]
        # Uso da última pergunta (tokens, custo, chamadas, ferramentas), para a interface e a avaliação.
        self.ultimo_uso: dict = {}
        self.tools = DEFINICOES + ([FERRAMENTA_CODIGO] if arquivo_csv_id else [])
        self.container_id: str | None = None
        self.container_expira: datetime | None = None
        # Histórico completo (inclui blocos de thinking e tool_use) — só cresce, nunca é editado.
        self.mensagens: list[dict] = []

    def perguntar(self, pergunta: str, ao_usar_ferramenta: Callable[[str, dict], None] | None = None) -> str:
        self.mensagens.append({"role": "user", "content": self._com_csv([{"type": "text", "text": pergunta}])})
        registros: list[dict] = []
        ferramentas_usadas: list[str] = []
        self.ferramentas.registros_api = []
        try:
            return self._loop(registros, ferramentas_usadas, ao_usar_ferramenta)
        finally:
            todos = registros + self.ferramentas.registros_api
            self.ultimo_uso = {
                **telemetria.somar(todos),
                "agente": telemetria.somar(registros),
                "ferramentas": ferramentas_usadas,
            }

    def _loop(self, registros, ferramentas_usadas, ao_usar_ferramenta) -> str:
        for _ in range(MAX_ITERACOES):
            parametros = {}
            if self._container_ativo():
                parametros["container"] = self.container_id  # mantém o CSV e o estado entre chamadas
            inicio = time.monotonic()
            resposta = self.client.beta.messages.create(
                model=MODELO,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                cache_control={"type": "ephemeral"},
                system=self.system,
                tools=self.tools,
                messages=self.mensagens,
                **parametros,
            )
            registros.append(telemetria.registrar(
                "agente", resposta, time.monotonic() - inicio, conversa_id=self.conversa_id
            ))
            self.mensagens.append({"role": "assistant", "content": resposta.content})
            if getattr(resposta, "container", None):
                self.container_id = resposta.container.id
                self.container_expira = resposta.container.expires_at

            # Código executado no servidor: só registramos e mostramos o que rodou.
            for bloco in resposta.content:
                if bloco.type == "server_tool_use":
                    ferramentas_usadas.append(bloco.name)
                    if ao_usar_ferramenta:
                        ao_usar_ferramenta(bloco.name, bloco.input)

            if resposta.stop_reason == "refusal":
                return "O modelo não pôde responder a essa pergunta. Tente reformulá-la."
            if resposta.stop_reason == "max_tokens":
                return _texto(resposta) + "\n\n_(resposta interrompida por limite de tamanho)_"
            if resposta.stop_reason == "pause_turn":
                continue  # o servidor pausou a execução de código; reenviar retoma de onde parou
            if resposta.stop_reason != "tool_use":
                return _texto(resposta)

            resultados = []
            for bloco in resposta.content:
                if bloco.type != "tool_use":
                    continue
                ferramentas_usadas.append(bloco.name)
                if ao_usar_ferramenta:
                    ao_usar_ferramenta(bloco.name, bloco.input)
                try:
                    conteudo, erro = self.ferramentas.executar(bloco.name, bloco.input), False
                except (ValueError, TypeError) as e:
                    conteudo, erro = f"Erro: {e}", True
                resultados.append({
                    "type": "tool_result", "tool_use_id": bloco.id,
                    "content": conteudo, "is_error": erro,
                })
            self.mensagens.append({"role": "user", "content": self._com_csv(resultados)})

        return "Não consegui concluir a análise dentro do limite de etapas. Tente uma pergunta mais específica."

    def _container_ativo(self) -> bool:
        return bool(
            self.container_id and self.container_expira
            and self.container_expira > datetime.now(timezone.utc) + timedelta(minutes=2)
        )

    def _com_csv(self, blocos: list[dict]) -> list[dict]:
        """Anexa o CSV à mensagem enquanto não há ambiente de execução ativo.

        O arquivo só é montado quando o container_upload está na mensagem mais recente, e o
        ambiente só nasce quando algum código roda; por isso ele vai em cada mensagem até lá
        (e de novo quando o ambiente expira).
        """
        if self.arquivo_csv_id and not self._container_ativo():
            return blocos + [{"type": "container_upload", "file_id": self.arquivo_csv_id}]
        return blocos


def _texto(resposta) -> str:
    return "\n".join(b.text for b in resposta.content if b.type == "text").strip()
