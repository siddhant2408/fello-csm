FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
WORKDIR /srv/fello
ENV UV_LINK_MODE=copy OPENBLAS_NUM_THREADS=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY app ./app
RUN useradd --uid 10001 --create-home fello && mkdir data && chown fello:fello /srv/fello/data
USER fello
ENV PATH="/srv/fello/.venv/bin:$PATH"
CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
