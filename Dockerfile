FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IOT_SIM_CONFIG=/config/devices.yaml \
    IOT_SIM_DATABASE=/data/iot-simulator.db \
    IOT_SIM_WEB_HOST=0.0.0.0

WORKDIR /app
COPY requirements.txt requirements.lock pyproject.toml THIRD_PARTY_NOTICES.md ./
RUN pip install --no-cache-dir -r requirements.lock
COPY iot_sim ./iot_sim
COPY config/example.yaml /config/devices.yaml
RUN pip install --no-cache-dir --no-deps .

VOLUME ["/data"]
EXPOSE 8080 4840 1502
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/status', timeout=2)"
ENTRYPOINT ["iot-sim", "serve"]
