"""Ferramentas que o agente pode chamar: busca semântica, agregações, contagem por critério e leitura por ID."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import anthropic
import pandas as pd

import config
import telemetria
from dados import IndiceSemantico

AGRUPAMENTOS = ["Estado", "Cidade", "Data", "Semana", "Mes", "DiaSemana"]
TEMPORAIS = {"Data", "Semana", "Mes"}
DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

# Classificação por critério: modelo barato, uma chamada por reclamação, com cache em disco.
MODELO_CLASSIFICADOR = config.MODELO_CLASSIFICADOR
MAX_CLASSIFICACOES = 500
CHAMADAS_PARALELAS = 8
CACHE_CRITERIOS = config.INDICE_DIR / "classificacoes_criterio.json"
MAX_RECLAMACOES_LISTADAS = 40

SYSTEM_CLASSIFICADOR = """Você verifica se uma reclamação de cliente da Localiza (Reclame Aqui) se enquadra em um critério.
Responda atende=true só se o texto tratar claramente do critério; menção de passagem ou caso duvidoso = false.
Na justificativa, uma frase curta em português citando o que no texto levou à decisão."""

SCHEMA_CLASSIFICACAO = {
    "type": "object",
    "properties": {
        "atende": {"type": "boolean"},
        "justificativa": {"type": "string"},
    },
    "required": ["atende", "justificativa"],
    "additionalProperties": False,
}

_FILTROS_SCHEMA = {
    "estado": {"type": "string", "description": "Sigla da UF, ex.: SP."},
    "cidade": {"type": "string", "description": "Nome da cidade, ex.: Contagem."},
    "data_inicio": {"type": "string", "description": "Data inicial inclusiva (AAAA-MM-DD)."},
    "data_fim": {"type": "string", "description": "Data final inclusiva (AAAA-MM-DD)."},
    "contem_termo": {
        "type": "string",
        "description": "Só reclamações cujo título ou texto contém este termo literal (sem diferenciar maiúsculas).",
    },
}

_AGRUPAMENTO_SCHEMA = {
    "agrupar_por": {
        "type": "string",
        "enum": AGRUPAMENTOS,
        "description": (
            "Semana = semanas de segunda a domingo (valor = segunda-feira). "
            "DiaSemana = segunda a domingo. Em Data, Semana e Mes os períodos sem "
            "reclamação aparecem com quantidade 0."
        ),
    },
    "top_n": {"type": "integer", "description": "Limita aos N grupos com mais reclamações (padrão: todos)."},
    "ordenar_por": {
        "type": "string",
        "enum": ["quantidade", "valor"],
        "description": "quantidade = maiores primeiro; valor = ordem cronológica/alfabética. Padrão: valor para agrupamentos de tempo, quantidade para os demais.",
    },
}

DEFINICOES = [
    {
        "name": "buscar_reclamacoes",
        "description": (
            "Busca semântica nas reclamações (título + texto). Use para encontrar exemplos "
            "e entender do que tratam as reclamações (ex.: 'cobrança após devolução'). "
            "Retorna as mais parecidas com a consulta, cada uma com o trecho que mais casou. "
            "O score só ordena; não serve para contar. Para contar por assunto, use contar_por_criterio."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "consulta": {"type": "string", "description": "O que procurar, em linguagem natural."},
                "top_k": {"type": "integer", "description": "Quantos resultados retornar (padrão 8, máx. 30)."},
                **_FILTROS_SCHEMA,
            },
            "required": ["consulta"],
            "additionalProperties": False,
        },
    },
    {
        "name": "contar_reclamacoes",
        "description": (
            "Contagem exata de reclamações, com filtros e agrupamento opcional. Use para "
            "totais, rankings, distribuição e evolução no tempo (por estado, cidade, dia, "
            "semana, mês ou dia da semana). Cada grupo traz quantidade e percentual do total. "
            "'estatisticas' traz média, mediana, mínimo e máximo por grupo (ex.: média por dia). "
            "'empatados_no_corte' = quantos grupos têm a mesma quantidade do último do ranking. "
            "Com 'contem_termo' conta menções literais de uma palavra/expressão."
        ),
        "input_schema": {
            "type": "object",
            "properties": {**_AGRUPAMENTO_SCHEMA, **_FILTROS_SCHEMA},
            "additionalProperties": False,
        },
    },
    {
        "name": "contar_por_criterio",
        "description": (
            "Conta reclamações que tratam de um assunto descrito em linguagem natural "
            "(ex.: 'boleto emitido indevidamente', 'carro entregue com defeito'). Um modelo "
            "lê CADA reclamação do filtro e decide se atende ao critério, então a contagem "
            "cobre todas, não só as mais parecidas. Aceita os mesmos filtros e agrupamentos "
            "de contar_reclamacoes. Retorna total, percentual, grupos e a justificativa de "
            "cada reclamação que atende. É mais lenta: use filtros de data/local quando a "
            "pergunta tiver. Descreva o critério de forma precisa e autossuficiente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "criterio": {"type": "string", "description": "O assunto a verificar em cada reclamação."},
                **_AGRUPAMENTO_SCHEMA,
                **_FILTROS_SCHEMA,
            },
            "required": ["criterio"],
            "additionalProperties": False,
        },
    },
    {
        "name": "obter_reclamacao",
        "description": "Retorna o texto completo de uma reclamação pelo ID.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
]


class Ferramentas:
    def __init__(self, df: pd.DataFrame, indice: IndiceSemantico | None):
        self.df = df
        self.indice = indice
        self._cliente: anthropic.Anthropic | None = None
        self._cache: dict | None = None
        # Chamadas à API feitas pelas ferramentas; o agente recolhe para somar o custo da pergunta.
        self.registros_api: list[dict] = []

    def executar(self, nome: str, entrada: dict) -> str:
        """Executa a ferramenta e devolve JSON. Erros de uso sobem como ValueError."""
        metodos = {
            "buscar_reclamacoes": self.buscar_reclamacoes,
            "contar_reclamacoes": self.contar_reclamacoes,
            "contar_por_criterio": self.contar_por_criterio,
            "obter_reclamacao": self.obter_reclamacao,
        }
        if nome not in metodos:
            raise ValueError(f"Ferramenta desconhecida: {nome}")
        return json.dumps(metodos[nome](**entrada), ensure_ascii=False, default=str)

    # ---------- filtros e agrupamento ----------

    def _mascara_filtros(
        self, estado=None, cidade=None, data_inicio=None, data_fim=None, contem_termo=None
    ) -> pd.Series:
        m = pd.Series(True, index=self.df.index)
        if estado:
            m &= self.df["Estado"].str.upper() == estado.strip().upper()
        if cidade:
            m &= self.df["Cidade"].str.casefold() == cidade.strip().casefold()
        if data_inicio:
            m &= self.df["Data"] >= pd.to_datetime(data_inicio)
        if data_fim:
            m &= self.df["Data"] <= pd.to_datetime(data_fim)
        if contem_termo:
            texto = self.df["titulo"].fillna("") + " " + self.df["Texto_Reclamacao"].fillna("")
            m &= texto.str.contains(contem_termo.strip(), case=False, regex=False)
        return m

    def _periodo(self, data_inicio=None, data_fim=None) -> tuple[pd.Timestamp, pd.Timestamp]:
        """Período coberto: o do filtro, limitado ao intervalo que existe no dataset."""
        inicio = max(pd.to_datetime(data_inicio), self.df["Data"].min()) if data_inicio else self.df["Data"].min()
        fim = min(pd.to_datetime(data_fim), self.df["Data"].max()) if data_fim else self.df["Data"].max()
        return inicio, fim

    @staticmethod
    def _chaves(sub: pd.DataFrame, agrupar_por: str) -> pd.Series:
        d = sub["Data"]
        if agrupar_por == "Data":
            return d.dt.strftime("%Y-%m-%d")
        if agrupar_por == "Semana":
            return d.dt.to_period("W-SUN").dt.start_time.dt.strftime("%Y-%m-%d")
        if agrupar_por == "Mes":
            return sub["Mes"]
        if agrupar_por == "DiaSemana":
            return d.dt.dayofweek.map(lambda i: DIAS_SEMANA[i])
        return sub[agrupar_por]

    @staticmethod
    def _todos_os_periodos(agrupar_por: str, inicio: pd.Timestamp, fim: pd.Timestamp) -> list[str]:
        if agrupar_por == "Data":
            return list(pd.date_range(inicio, fim, freq="D").strftime("%Y-%m-%d"))
        if agrupar_por == "Semana":
            return list(pd.period_range(inicio, fim, freq="W-SUN").start_time.strftime("%Y-%m-%d"))
        return list(pd.period_range(inicio, fim, freq="M").strftime("%Y-%m"))

    def _agrupar(self, sub, agrupar_por, top_n=None, ordenar_por=None, data_inicio=None, data_fim=None) -> dict:
        if agrupar_por not in AGRUPAMENTOS:
            raise ValueError(f"agrupar_por deve ser um de {AGRUPAMENTOS}")
        contagem = self._chaves(sub, agrupar_por).value_counts()
        inicio, fim = self._periodo(data_inicio, data_fim)
        if agrupar_por in TEMPORAIS and inicio <= fim:
            # Períodos sem reclamação contam como 0 (importante para médias).
            todos = self._todos_os_periodos(agrupar_por, inicio, fim)
            contagem = contagem.reindex(sorted(set(todos) | set(contagem.index)), fill_value=0)

        resposta = {}
        if len(contagem):
            resposta["estatisticas"] = {
                "grupos": len(contagem),
                "media": round(float(contagem.mean()), 2),
                "mediana": float(contagem.median()),
                "minimo": int(contagem.min()),
                "maximo": int(contagem.max()),
            }

        # Ranking estável: mais reclamações primeiro, empates em ordem de valor.
        ranking = contagem.sort_index().sort_values(ascending=False, kind="stable")
        if top_n:
            top_n = int(top_n)
            corte = ranking.iloc[top_n - 1] if len(ranking) >= top_n else None
            resposta["empatados_no_corte"] = int((ranking == corte).sum()) if corte is not None else 0
            ranking = ranking.head(top_n)

        if ordenar_por is None:
            ordenar_por = "valor" if agrupar_por in TEMPORAIS | {"DiaSemana"} else "quantidade"
        if ordenar_por == "valor":
            if agrupar_por == "DiaSemana":
                ranking = ranking.sort_index(key=lambda idx: idx.map(DIAS_SEMANA.index))
            else:
                ranking = ranking.sort_index()

        total = len(sub)
        grupos = []
        for valor, qtd in ranking.items():
            g = {"valor": valor, "quantidade": int(qtd), "percentual": round(100 * qtd / total, 1) if total else 0.0}
            if agrupar_por == "Semana":
                ini_semana = pd.Timestamp(valor)
                fim_semana = ini_semana + pd.Timedelta(days=6)
                g["ate"] = fim_semana.strftime("%Y-%m-%d")
                g["semana_parcial"] = bool(ini_semana < inicio or fim_semana > fim)
            grupos.append(g)
        resposta["grupos"] = grupos
        return resposta

    # ---------- ferramentas ----------

    def buscar_reclamacoes(self, consulta, top_k=8, **filtros):
        top_k = max(1, min(int(top_k), 30))
        m = self._mascara_filtros(**filtros).to_numpy()
        scores, trechos = self.indice.buscar(consulta)
        candidatos = [i for i in scores.argsort()[::-1] if m[i]]
        resultados = []
        for i in candidatos[:top_k]:
            linha = self.df.iloc[i]
            resultados.append({
                "id": linha["ID"],
                "score": round(float(scores[i]), 3),
                "data": linha["Data"].date().isoformat(),
                "cidade": linha["Cidade"],
                "estado": linha["Estado"],
                "titulo": linha["titulo"],
                "trecho_relevante": trechos[i],
            })
        return {"total_no_filtro": int(m.sum()), "resultados": resultados}

    def contar_reclamacoes(self, agrupar_por=None, top_n=None, ordenar_por=None, **filtros):
        sub = self.df[self._mascara_filtros(**filtros)]
        resposta = {"total": len(sub)}
        if agrupar_por:
            resposta.update(self._agrupar(
                sub, agrupar_por, top_n, ordenar_por, filtros.get("data_inicio"), filtros.get("data_fim")
            ))
        return resposta

    def contar_por_criterio(self, criterio, agrupar_por=None, top_n=None, ordenar_por=None, **filtros):
        criterio = " ".join(str(criterio).split())
        if not criterio:
            raise ValueError("Informe o critério.")
        sub = self.df[self._mascara_filtros(**filtros)]
        if len(sub) > MAX_CLASSIFICACOES:
            raise ValueError(
                f"O filtro tem {len(sub)} reclamações (limite {MAX_CLASSIFICACOES}). "
                "Restrinja por data, local ou termo."
            )

        decisoes = self._classificar_todas(criterio, sub)
        atende = sub[sub["ID"].map(lambda i: bool(decisoes[i] and decisoes[i]["atende"]))]
        indeterminadas = sum(1 for i in sub["ID"] if decisoes[i] is None)

        resposta = {
            "criterio": criterio,
            "avaliadas": len(sub),
            "atendem": len(atende),
            "percentual": round(100 * len(atende) / len(sub), 1) if len(sub) else 0.0,
        }
        if indeterminadas:
            resposta["nao_classificadas"] = indeterminadas
        if agrupar_por:
            resposta.update(self._agrupar(
                atende, agrupar_por, top_n, ordenar_por, filtros.get("data_inicio"), filtros.get("data_fim")
            ))
        resposta["reclamacoes"] = [
            {
                "id": r["ID"], "data": r["Data"].date().isoformat(), "cidade": r["Cidade"],
                "estado": r["Estado"], "titulo": r["titulo"],
                "justificativa": decisoes[r["ID"]]["justificativa"],
            }
            for _, r in atende.sort_values("Data").head(MAX_RECLAMACOES_LISTADAS).iterrows()
        ]
        if len(atende) > MAX_RECLAMACOES_LISTADAS:
            resposta["reclamacoes_omitidas"] = len(atende) - MAX_RECLAMACOES_LISTADAS
        return resposta

    def obter_reclamacao(self, id):
        linha = self.df[self.df["ID"] == str(id).strip()]
        if linha.empty:
            raise ValueError(f"Nenhuma reclamação com ID {id}")
        r = linha.iloc[0]
        return {
            "id": r["ID"], "data": r["Data"].date().isoformat(), "cidade": r["Cidade"],
            "estado": r["Estado"], "titulo": r["titulo"], "texto": r["Texto_Reclamacao"],
        }

    # ---------- classificação por critério ----------

    def _carregar_cache(self) -> dict:
        if self._cache is None:
            try:
                self._cache = json.loads(CACHE_CRITERIOS.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                self._cache = {}
        return self._cache

    def _classificar_todas(self, criterio: str, sub: pd.DataFrame) -> dict[str, dict | None]:
        """Decisão por ID. None = o modelo não devolveu uma classificação válida."""
        cache = self._carregar_cache()
        chave = f"{MODELO_CLASSIFICADOR}|{criterio.casefold()}"
        conhecidas = cache.setdefault(chave, {})
        pendentes = [r for _, r in sub.iterrows() if r["ID"] not in conhecidas]

        if pendentes:
            with ThreadPoolExecutor(CHAMADAS_PARALELAS) as pool:
                for r, decisao in zip(pendentes, pool.map(lambda r: self._classificar(criterio, r), pendentes)):
                    if decisao is not None:
                        conhecidas[r["ID"]] = decisao
            CACHE_CRITERIOS.parent.mkdir(parents=True, exist_ok=True)
            CACHE_CRITERIOS.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

        return {i: conhecidas.get(i) for i in sub["ID"]}

    def _classificar(self, criterio: str, r: pd.Series) -> dict | None:
        if self._cliente is None:
            self._cliente = anthropic.Anthropic(max_retries=4)
        inicio = time.monotonic()
        resposta = self._cliente.messages.create(
            model=MODELO_CLASSIFICADOR,
            max_tokens=2000,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": SCHEMA_CLASSIFICACAO},
            },
            system=SYSTEM_CLASSIFICADOR,
            messages=[{
                "role": "user",
                "content": (
                    f"Critério: {criterio}\n\n"
                    f"Título: {r['titulo']}\n"
                    f"Texto: {r['Texto_Reclamacao']}"
                ),
            }],
        )
        self.registros_api.append(
            telemetria.registrar("classificador", resposta, time.monotonic() - inicio, reclamacao_id=r["ID"])
        )
        if resposta.stop_reason != "end_turn":
            return None  # recusa ou corte: fica fora da contagem e é informado como não classificada
        texto = next((b.text for b in resposta.content if b.type == "text"), "")
        try:
            decisao = json.loads(texto)
        except json.JSONDecodeError:
            return None
        return {"atende": bool(decisao["atende"]), "justificativa": str(decisao["justificativa"])}
