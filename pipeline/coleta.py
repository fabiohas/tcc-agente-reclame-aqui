"""Tarefa de coleta do pipeline: roda o coletor oficial (reclameaqui_scraper.py).

O CSV vai para a landing (entrada da bronze) e o JSON bruto para data/bruto/.
A coleta nunca derruba o pipeline: qualquer falha (Chrome ausente, site fora do ar,
bloqueio) vira um status, e o pipeline segue com os arquivos que já estão na landing.
"""

import logging

import config

log = logging.getLogger(__name__)


def executar_coleta(ids_conhecidos: set[str] | None = None) -> dict:
    """Ponto de entrada do pipeline e do DAG. Devolve um resumo da coleta."""
    if not config.RA_COLETA_ATIVA:
        return {"status": "desativada", "novas": 0}
    try:
        # Importado aqui para o Selenium só ser exigido quando a coleta roda.
        import reclameaqui_scraper as scraper

        brutas, df = scraper.coletar(
            empresa=config.RA_EMPRESA,
            max_paginas=config.RA_MAX_PAGINAS,
            headless=True,
            ids_conhecidos=ids_conhecidos,
        )
        caminhos = scraper.salvar(brutas, df, config.RA_EMPRESA, config.LANDING_DIR, config.BRUTO_DIR)
    except Exception as e:  # a coleta é opcional: o pipeline continua com a landing
        log.warning("Coleta falhou: %s: %s. Seguindo só com os arquivos da landing.", type(e).__name__, e)
        return {"status": "erro", "novas": 0, "motivo": f"{type(e).__name__}: {e}"}

    falhas = sum(1 for r in brutas if r.get("texto") == scraper.MSG_FALHA)
    if falhas:
        log.warning("Coleta: %d de %d páginas não puderam ser lidas.", falhas, len(brutas))
    return {
        "status": "ok",
        "links": len(brutas),
        "falhas": falhas,
        "novas": len(df),
        "arquivo": str(caminhos["csv"]) if caminhos["csv"] else None,
        "bruto": str(caminhos["json"]) if caminhos["json"] else None,
    }
