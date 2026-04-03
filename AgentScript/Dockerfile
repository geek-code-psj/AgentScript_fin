FROM node:22-bookworm-slim AS dashboard-build

WORKDIR /app/dashboard

COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci

COPY dashboard/ ./
RUN npm run build


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY docs ./docs
COPY examples ./examples
COPY benchmarks ./benchmarks
COPY evals ./evals
COPY vscode ./vscode
COPY --from=dashboard-build /app/dashboard/dist ./dashboard/dist

RUN pip install .[dashboard,otel]

EXPOSE 8000
VOLUME ["/data"]

CMD ["python", "-m", "agentscript", "dashboard", "/data/agentscript.sqlite", "--host", "0.0.0.0", "--port", "8000"]
