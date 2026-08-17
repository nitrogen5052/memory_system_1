FROM python:3.11-slim

WORKDIR /app

COPY . .

RUN python -m pip install --no-cache-dir ".[dev]" \
    && python -m pytest -v \
    && cp -R examples/multi-project-workspace /tmp/memory-system-workspace \
    && memory-system plan --workspace /tmp/memory-system-workspace \
    && memory-system apply --yes --workspace /tmp/memory-system-workspace \
    && memory-system verify --workspace /tmp/memory-system-workspace \
    && reapply_output="$(memory-system apply --yes --workspace /tmp/memory-system-workspace)" \
    && test "$reapply_output" = "No changes"
