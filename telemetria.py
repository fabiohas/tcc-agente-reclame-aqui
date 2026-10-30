"""Registro de cada chamada à API da Anthropic: tokens, custo estimado e duração.

Cada chamada vira uma linha em data/logs/chamadas_AAAA-MM-DD.jsonl. O custo é uma estimativa
pelos preços em config.PRECOS_POR_MILHAO e não inclui o tempo de uso da execução de código.
"""

import json
import logging
import threading
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)
_trava = threading.Lock()


def _custo(modelo: str, uso: dict) -> float | None:
    precos = config.PRECOS_POR_MILHAO.get(modelo)
    if precos is None:
        return None
    entrada, saida, leitura_cache, escrita_cache = precos
    return round((
        uso["input_tokens"] * entrada
        + uso["output_tokens"] * saida
        + uso["cache_read_input_tokens"] * leitura_cache
        + uso["cache_creation_input_tokens"] * escrita_cache
    ) / 1_000_000, 6)


def registrar(origem: str, resposta, duracao_s: float, **contexto) -> dict:
    """Grava a chamada no log e devolve o registro (tokens + custo)."""
    u = resposta.usage
    uso = {
        "input_tokens": u.input_tokens or 0,
        "output_tokens": u.output_tokens or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }
    registro = {
        "momento": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origem": origem,
        "modelo": resposta.model,
        "stop_reason": resposta.stop_reason,
        "duracao_s": round(duracao_s, 2),
        **uso,
        "custo_usd": _custo(resposta.model, uso),
        **contexto,
    }
    try:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        arquivo = config.LOGS_DIR / f"chamadas_{registro['momento'][:10]}.jsonl"
        with _trava, open(arquivo, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("Não foi possível gravar o log de chamadas: %s", e)
    return registro


def somar(registros: list[dict]) -> dict:
    """Totais de uma lista de registros (ex.: todas as chamadas de uma pergunta)."""
    total = {k: sum(r[k] for r in registros) for k in
             ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"]}
    custos = [r["custo_usd"] for r in registros if r["custo_usd"] is not None]
    total["custo_usd"] = round(sum(custos), 6)
    total["chamadas"] = len(registros)
    return total
