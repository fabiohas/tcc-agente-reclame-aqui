"""Roda a avaliação do agente e grava o relatório em data/avaliacao/<execução>/.

Uso:
  python -m avaliacao.rodar                      # todos os casos, 1 vez cada
  python -m avaliacao.rodar --casos total pct_sp # só alguns casos
  python -m avaliacao.rodar --repeticoes 3       # mede a consistência (o modelo não é determinístico)
  python -m avaliacao.rodar --listar             # mostra os casos sem chamar a API

Cada pergunta usa uma conversa nova. "Hoje" fica fixo no dia seguinte ao último dado, para que
perguntas relativas ("últimas 2 semanas") tenham gabarito estável.
"""

import argparse
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

import config
from avaliacao import corretores
from avaliacao.casos import CASOS, Caso


def corrigir(caso: Caso, df: pd.DataFrame, resposta: str, ferramentas_usadas: list[str], juiz=corretores.julgar):
    falhas, extras = [], {}
    if caso.numeros:
        falhas += corretores.conferir_numeros(resposta, caso.numeros(df), caso.tolerancia_relativa)
    if caso.ordem:
        falhas += corretores.conferir_ordem(resposta, caso.ordem(df))
    if caso.checar_ids:
        falhas += corretores.conferir_ids(resposta, set(df["ID"]))
    if caso.ferramentas and not set(caso.ferramentas) & set(ferramentas_usadas):
        falhas.append(f"não usou {caso.ferramentas}")
    if caso.rubrica:
        aprovado, justificativa, registro = juiz(caso.pergunta, resposta, caso.rubrica(df))
        extras = {"juiz": justificativa, "custo_juiz_usd": registro["custo_usd"]}
        if not aprovado:
            falhas.append(f"juiz: {justificativa}")
    return falhas, extras


def executar(ids: list[str] | None, repeticoes: int) -> dict:
    from agente import Agente, enviar_csv
    from dados import IndiceSemantico, carregar_dataframe
    from ferramentas import Ferramentas

    df = carregar_dataframe()
    ferramentas = Ferramentas(df, IndiceSemantico(df))
    arquivo_csv = enviar_csv(df) if config.USAR_EXECUCAO_CODIGO else None
    hoje = (df["Data"].max() + pd.Timedelta(days=1)).date()
    casos = [c for c in CASOS if not ids or c.id in ids]

    pasta = config.AVALIACAO_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta.mkdir(parents=True, exist_ok=True)
    resultados = []
    with open(pasta / "resultados.jsonl", "w", encoding="utf-8") as saida:
        for caso in casos:
            for rodada in range(1, repeticoes + 1):
                agente = Agente(df, ferramentas, arquivo_csv, hoje=hoje)
                inicio = time.monotonic()
                try:
                    resposta = agente.perguntar(caso.pergunta)
                    erro = None
                except Exception as e:  # noqa: BLE001 - um caso com erro não deve parar a avaliação
                    resposta, erro = "", f"{type(e).__name__}: {e}"
                duracao = time.monotonic() - inicio
                uso = agente.ultimo_uso
                falhas, extras = (["erro: " + erro], {}) if erro else corrigir(caso, df, resposta, uso.get("ferramentas", []))
                r = {
                    "caso": caso.id, "categoria": caso.categoria, "rodada": rodada,
                    "aprovado": not falhas, "falhas": falhas, "duracao_s": round(duracao, 1),
                    "custo_usd": uso.get("custo_usd", 0.0), "chamadas": uso.get("chamadas", 0),
                    "ferramentas": uso.get("ferramentas", []), "pergunta": caso.pergunta,
                    "resposta": resposta, **extras,
                }
                resultados.append(r)
                saida.write(json.dumps(r, ensure_ascii=False) + "\n")
                print(f"{'✅' if r['aprovado'] else '❌'} {caso.id} [{rodada}] {r['duracao_s']}s "
                      f"US$ {r['custo_usd']:.4f} {'; '.join(falhas)}", flush=True)

    resumo = resumir(resultados)
    (pasta / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    (pasta / "relatorio.md").write_text(relatorio_md(resumo, resultados), encoding="utf-8")
    resumo["pasta"] = str(pasta)
    return resumo


def resumir(resultados: list[dict]) -> dict:
    por_categoria = defaultdict(list)
    for r in resultados:
        por_categoria[r["categoria"]].append(r["aprovado"])
    duracoes = [r["duracao_s"] for r in resultados]
    return {
        "execucoes": len(resultados),
        "acerto_pct": round(100 * sum(r["aprovado"] for r in resultados) / len(resultados), 1) if resultados else 0,
        "acerto_por_categoria_pct": {c: round(100 * sum(v) / len(v), 1) for c, v in sorted(por_categoria.items())},
        "custo_total_usd": round(sum(r["custo_usd"] + r.get("custo_juiz_usd", 0) for r in resultados), 4),
        "custo_medio_agente_usd": round(statistics.mean(r["custo_usd"] for r in resultados), 4) if resultados else 0,
        "duracao_mediana_s": round(statistics.median(duracoes), 1) if duracoes else 0,
        "duracao_max_s": max(duracoes, default=0),
        "modelo_agente": config.MODELO_AGENTE,
    }


def relatorio_md(resumo: dict, resultados: list[dict]) -> str:
    linhas = [
        "# Avaliação do agente", "",
        f"- Modelo do agente: `{resumo['modelo_agente']}`",
        f"- Execuções: {resumo['execucoes']}",
        f"- **Acerto: {resumo['acerto_pct']}%**",
        f"- Custo total (agente + juiz): US$ {resumo['custo_total_usd']:.4f}",
        f"- Tempo por pergunta: mediana {resumo['duracao_mediana_s']}s, máximo {resumo['duracao_max_s']}s", "",
        "## Acerto por categoria", "", "| Categoria | Acerto |", "|---|---|",
        *[f"| {c} | {p}% |" for c, p in resumo["acerto_por_categoria_pct"].items()], "",
        "## Casos", "", "| Caso | Rodada | Resultado | Tempo (s) | Custo (US$) | Falhas |", "|---|---|---|---|---|---|",
    ]
    for r in resultados:
        falhas = "; ".join(r["falhas"]).replace("|", "/")
        linhas.append(f"| {r['caso']} | {r['rodada']} | {'✅' if r['aprovado'] else '❌'} | "
                      f"{r['duracao_s']} | {r['custo_usd']:.4f} | {falhas} |")
    return "\n".join(linhas) + "\n"


if __name__ == "__main__":
    load_dotenv()
    config.configurar_logging()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--casos", nargs="*", help="IDs dos casos (padrão: todos)")
    parser.add_argument("--repeticoes", type=int, default=1)
    parser.add_argument("--listar", action="store_true")
    args = parser.parse_args()
    if args.listar:
        for c in CASOS:
            print(f"{c.id:<24} {c.categoria:<10} {c.pergunta}")
    else:
        print(json.dumps(executar(args.casos, args.repeticoes), ensure_ascii=False, indent=2))
