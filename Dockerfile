FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY static ./static
COPY assets ./assets
COPY docker/bin/host_cli.py /usr/local/bin/host_cli.py

RUN sed -i 's/\r$//' /usr/local/bin/host_cli.py \
    && chmod 755 /usr/local/bin/host_cli.py \
    && ln -sf host_cli.py /usr/local/bin/claude \
    && ln -sf host_cli.py /usr/local/bin/codex \
    && ln -sf host_cli.py /usr/local/bin/agy \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8787

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
