FROM python:3.12-slim
WORKDIR /app
COPY services/asr-base/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/asr-base/download_model.py ./download_model.py
RUN python download_model.py
COPY services/asr-base/server.py ./server.py
ENV HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=5 --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready',timeout=2)"
CMD ["python","server.py"]
