FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY eaaf ./eaaf
RUN mkdir /data && chown 65532:65532 /data
USER 65532:65532
