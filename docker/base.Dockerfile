FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
RUN pip install -e ".[dev]" || pip install .

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

ENTRYPOINT ["python", "-m", "loophedge"]
