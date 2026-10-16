# Imagem de produção do Sistema de Orçamentos Públicos (FastAPI + SQLite).
# Usada pelo Fly.io (e por qualquer host com Docker). O frontend é servido pelo
# próprio FastAPI (main.py monta ../frontend), por isso copiamos backend/ e frontend/.
FROM python:3.12-slim

# Evita .pyc e deixa o log sair na hora
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATA_DIR=/data

WORKDIR /app

# Dependências primeiro (melhor cache de camadas)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Código
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/

# Diretório de dados persistente (volume do Fly montado em /data)
RUN mkdir -p /data

WORKDIR /app/backend

# O servidor sobe na porta definida por $PORT (Fly injeta/espera 8080).
CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
