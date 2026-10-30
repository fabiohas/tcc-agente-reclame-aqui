"""Loop do agente com um cliente falso (sem chamar a API)."""

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import config
from agente import Agente
from ferramentas import Ferramentas


def resposta(blocos, stop_reason, container=None):
    uso = NS(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return NS(content=blocos, stop_reason=stop_reason, usage=uso, model="claude-opus-5-5", container=container)


class ClienteFalso:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.pedidos = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.pedidos.append({**kw, "messages": list(kw["messages"])})
        return self.respostas.pop(0)


def test_executa_ferramenta_e_devolve_resposta_final(df_ouro):
    cliente = ClienteFalso([
        resposta([NS(type="tool_use", id="t1", name="contar_reclamacoes", input={"agrupar_por": "Estado", "top_n": 1})], "tool_use"),
        resposta([NS(type="text", text="MG e SP empatam com 2.")], "end_turn"),
    ])
    agente = Agente(df_ouro, Ferramentas(df_ouro, None), hoje=date(2026, 9, 11), client=cliente)
    usadas = []
    assert agente.perguntar("Top estado?", lambda n, e: usadas.append(n)) == "MG e SP empatam com 2."

    resultado = cliente.pedidos[1]["messages"][-1]["content"][0]
    assert resultado["type"] == "tool_result" and not resultado["is_error"]
    assert json.loads(resultado["content"])["empatados_no_corte"] == 2
    assert usadas == ["contar_reclamacoes"]
    assert agente.ultimo_uso["chamadas"] == 2
    assert agente.ultimo_uso["custo_usd"] == round(2 * (1000 * 4 + 100 * 20) / 1e6, 6)
    assert list(config.LOGS_DIR.glob("chamadas_*.jsonl"))


def test_erro_da_ferramenta_volta_para_o_modelo(df_ouro):
    cliente = ClienteFalso([
        resposta([NS(type="tool_use", id="t1", name="obter_reclamacao", input={"id": "999"})], "tool_use"),
        resposta([NS(type="text", text="Não existe.")], "end_turn"),
    ])
    Agente(df_ouro, Ferramentas(df_ouro, None), client=cliente).perguntar("Mostre a 999")
    resultado = cliente.pedidos[1]["messages"][-1]["content"][0]
    assert resultado["is_error"] and "999" in resultado["content"]


def test_data_de_hoje_vai_no_prompt(df_ouro):
    agente = Agente(df_ouro, Ferramentas(df_ouro, None), hoje=date(2026, 10, 28), client=ClienteFalso([]))
    assert "Hoje é 2026-10-28" in agente.system


def test_csv_vai_em_cada_mensagem_ate_existir_ambiente_ativo(df_ouro):
    container = NS(id="c1", expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    cliente = ClienteFalso([
        resposta([NS(type="tool_use", id="t1", name="contar_reclamacoes", input={})], "tool_use"),
        resposta([NS(type="server_tool_use", name="bash_code_execution", input={"command": "ls"}),
                  NS(type="text", text="ok")], "end_turn", container=container),
        resposta([NS(type="text", text="ok 2")], "end_turn", container=container),
    ])
    agente = Agente(df_ouro, Ferramentas(df_ouro, None), arquivo_csv_id="file_1", client=cliente)
    agente.perguntar("primeira")
    assert agente.ultimo_uso["ferramentas"] == ["contar_reclamacoes", "bash_code_execution"]
    agente.perguntar("segunda")

    def tem_csv(pedido):
        return any(b.get("type") == "container_upload" for b in pedido["messages"][-1]["content"])

    assert [tem_csv(p) for p in cliente.pedidos] == [True, True, False]
    assert "container" not in cliente.pedidos[1] and cliente.pedidos[2]["container"] == "c1"
    assert agente.ultimo_uso["ferramentas"] == []
