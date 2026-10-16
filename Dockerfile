FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY trader ./trader
RUN useradd -r -u 10001 trader && mkdir -p /data && chown trader /data
USER trader
CMD ["python", "-m", "trader", "paper"]
