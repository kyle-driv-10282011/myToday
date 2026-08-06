# Base image already bundles Chromium + all its OS-level deps at the exact
# version pinned in requirements.txt, so we don't need to hand-roll
# `playwright install --with-deps` ourselves.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

# xvfb + x11vnc + noVNC (via websockify) let the interactive Slack login
# (headed Chromium, manual MFA) be viewed and controlled remotely through a
# browser tab, since the container itself has no physical display. All from
# apt (not `git clone`/pip from GitHub/PyPI directly) since this network's
# Zscaler TLS inspection isn't trusted inside the container and breaks raw
# HTTPS fetches from arbitrary hosts; the Ubuntu package mirror is fine.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py synergy_lock.py calendar.html ./
COPY Images ./Images

COPY docker/supervisord.conf /etc/supervisor/conf.d/mytoday.conf
COPY docker/x11vnc-entrypoint.sh /usr/local/bin/x11vnc-entrypoint.sh
RUN chmod +x /usr/local/bin/x11vnc-entrypoint.sh

# config.py and feeds.json are never baked into the image (they hold
# secrets and are meant to be edited without a rebuild) -- mount them as
# volumes, see docker-compose.yml.

# Without this, Python block-buffers stdout when it's not a TTY (i.e. under
# supervisor), so `docker logs` shows nothing until the buffer fills.
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:99
EXPOSE 8080 6080

CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/mytoday.conf"]
