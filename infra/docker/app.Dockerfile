FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY pyproject.toml infra/docker/install_deps.py ./
RUN python3 install_deps.py && rm install_deps.py

COPY . .

EXPOSE 8000
