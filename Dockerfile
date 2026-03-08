FROM python:3.11-slim

# Dependências do sistema (git para instalar iqoptionapi)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. Instalar pacotes base com websocket-client 1.9.0
RUN pip install --no-cache-dir \
    flask==3.0.3 \
    flask-sqlalchemy==3.1.1 \
    pyjwt==2.8.0 \
    "numpy==1.26.4" \
    requests==2.32.3 \
    gunicorn==21.2.0 \
    "websocket-client==1.9.0" \
    "psutil>=5.9.0" \
    pylint

# 2. Instalar iqoptionapi SEM deps (ignora websocket-client==0.56)
RUN pip install --no-cache-dir --no-deps \
    "git+https://github.com/Lu-Yi-Hsun/iqoptionapi"

# Copiar código
COPY . .

# Usar shell form para que $PORT seja expandido em runtime
CMD gunicorn app:app --bind "0.0.0.0:${PORT:-8080}" --workers 1 --threads 4 --timeout 120
