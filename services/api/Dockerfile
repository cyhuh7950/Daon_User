FROM python:3.14-slim
WORKDIR /app
COPY services/api/pyproject.toml ./
COPY services/api/src ./src
RUN pip install --no-cache-dir .
ENV PYTHONUNBUFFERED=1
ENV DAON_API_BIND_HOST=0.0.0.0
ENV DAON_API_PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"
CMD ["python", "-m", "daon_user_api"]
