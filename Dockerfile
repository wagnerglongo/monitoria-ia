FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PASTA_DADOS=/dados

WORKDIR /app
COPY pyproject.toml README.md ./
COPY monitoria ./monitoria
RUN pip install ".[postgres]"

COPY rubricas ./rubricas
COPY exemplos ./exemplos

RUN useradd --create-home --uid 1000 monitoria \
 && mkdir -p /dados \
 && chown monitoria:monitoria /dados
USER monitoria

ENV RUBRICA=/app/rubricas/cobranca.json
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/saude')" || exit 1

CMD ["python", "-m", "monitoria", "web", "--host", "0.0.0.0", "--porta", "8000"]
