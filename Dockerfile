# Web dashboard for Token Monitor. Standard library only: no pip installs.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/data/home \
    TOKEN_MONITOR_HISTORY_DIR=/data/history

# /data holds the settings file, hidden.json, the cache and the snapshot history. It is world-writable
# so the container also works when compose runs it as your own uid (see docker-compose.yml).
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin monitor \
 && mkdir -p /data/home /data/history \
 && chown -R monitor:monitor /data \
 && chmod -R a+rwX /data

WORKDIR /app
COPY token_monitor/ /app/token_monitor/
COPY data/ /app/data/

USER monitor
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)"

CMD ["python3", "-m", "token_monitor.web", "--host", "0.0.0.0", "--port", "8080"]
