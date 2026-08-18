# Imagem usada para rodar a aplicação localmente (via `docker compose up`) ou
# em qualquer host que aceite um Dockerfile comum, com porta fixa em 8000. Ver
# `Dockerfile.vercel` para a variante usada no deploy na Vercel (porta via
# $PORT, storage em /tmp).
FROM python:3.12-slim

# tesseract-ocr-por: modelo de português — a maioria dos documentos trabalhistas
# brasileiros vem nesse idioma, e sem o pacote de idioma o Tesseract cai para
# inglês e erra sistematicamente acentos e formatação de data.
# libgl1: dependência de sistema do Pillow/OpenCV para processar as imagens
# renderizadas a partir do PDF antes de passar pelo Tesseract.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-por \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia só o requirements.txt primeiro e instala antes do resto do código:
# assim o Docker reaproveita essa camada em cache quando só o código muda,
# sem precisar reinstalar as dependências a cada build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web

# Cria um usuário sem privilégios de administrador e roda o container com
# ele: se alguém explorar uma falha na aplicação, o processo não tem poder
# de root dentro do container.
RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

ENV STORAGE_DIR=/app/data \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# O Docker (e ferramentas como docker-compose) usam isso para saber se o
# container está saudável, consultando o mesmo endpoint /healthz da API.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
