"""Dados para o agente: tabela ouro + índice de embeddings para busca semântica."""

import hashlib

import numpy as np
import pandas as pd

import config
from pipeline.ouro import ler_reclamacoes

PALAVRAS_POR_TRECHO = 60  # cabe no limite de 128 tokens do modelo de embeddings
SOBREPOSICAO = 15


def carregar_dataframe() -> pd.DataFrame:
    if not config.OURO_PARQUET.exists():
        raise FileNotFoundError(
            f"{config.OURO_PARQUET} não existe. Rode o pipeline antes: python -m pipeline.executar --sem-coleta"
        )
    return ler_reclamacoes()


def dividir_em_trechos(titulo: str, texto: str) -> list[str]:
    """O título vira um trecho próprio; o texto é fatiado em janelas com sobreposição."""
    palavras = str(texto).split()
    passo = PALAVRAS_POR_TRECHO - SOBREPOSICAO
    trechos = [str(titulo)]
    for i in range(0, max(len(palavras) - SOBREPOSICAO, 1), passo):
        trechos.append(" ".join(palavras[i:i + PALAVRAS_POR_TRECHO]))
    return trechos


class IndiceSemantico:
    """Embeddings de trechos normalizados em memória; score da reclamação = melhor trecho."""

    def __init__(self, df: pd.DataFrame):
        # Import tardio: o pipeline (Airflow) usa este pacote sem precisar do torch.
        from sentence_transformers import SentenceTransformer

        self.df = df
        self.modelo = SentenceTransformer(config.MODELO_EMBEDDINGS)
        self.trechos, self.dono = [], []
        for pos, (titulo, texto) in enumerate(zip(df["titulo"], df["Texto_Reclamacao"])):
            for t in dividir_em_trechos(titulo, texto):
                self.trechos.append(t)
                self.dono.append(pos)
        self.dono = np.array(self.dono)
        self.embeddings = self._carregar_ou_criar()

    def _assinatura(self) -> str:
        """Muda se mudar o modelo, o fatiamento, a ordem ou o texto de qualquer trecho."""
        h = hashlib.sha256(f"{config.MODELO_EMBEDDINGS}|{PALAVRAS_POR_TRECHO}|{SOBREPOSICAO}".encode())
        ids = self.df["ID"].to_numpy(dtype=str)[self.dono]
        for i, t in zip(ids, self.trechos):
            h.update(f"{i}\x00{t}\x01".encode())
        return h.hexdigest()[:16]

    def _carregar_ou_criar(self) -> np.ndarray:
        caminho = config.INDICE_DIR / f"embeddings_{self._assinatura()}.npy"
        if caminho.exists():
            return np.load(caminho)
        emb = self.modelo.encode(self.trechos, normalize_embeddings=True, show_progress_bar=True)
        config.INDICE_DIR.mkdir(parents=True, exist_ok=True)
        for antigo in config.INDICE_DIR.glob("embeddings_*.npy"):
            antigo.unlink()  # índices de versões anteriores dos dados
        np.save(caminho, emb)
        return emb

    def buscar(self, consulta: str) -> tuple[np.ndarray, list[str]]:
        """Por reclamação (ordem do DataFrame): score do melhor trecho e o texto desse trecho."""
        q = self.modelo.encode([consulta], normalize_embeddings=True)[0]
        sims = self.embeddings @ q
        scores = np.full(len(self.df), -1.0)
        melhor = np.zeros(len(self.df), dtype=int)
        for i, (dono, s) in enumerate(zip(self.dono, sims)):
            if s > scores[dono]:
                scores[dono], melhor[dono] = s, i
        return scores, [self.trechos[i] for i in melhor]


if __name__ == "__main__":
    # `python dados.py` pré-gera o índice a partir da camada ouro.
    df = carregar_dataframe()
    IndiceSemantico(df)
    print(f"Índice criado com {len(df)} reclamações em {config.INDICE_DIR}")
