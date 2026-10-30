# Interface Streamlit + agente.
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    HF_HOME=/app/data/modelos

WORKDIR /app
COPY requirements.txt requirements-pipeline.txt ./
# torch em versão só-CPU (a imagem fica ~2 GB menor que com CUDA).
RUN pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

COPY *.py ./
COPY pipeline ./pipeline
COPY avaliacao ./avaliacao

EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
