FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd -u 1000 -m wsmsync
USER wsmsync
WORKDIR /work
ENTRYPOINT ["wsmsync", "--config", "/work/config.toml"]
CMD ["sync"]
