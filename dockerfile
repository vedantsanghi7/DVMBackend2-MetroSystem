# Dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# system deps for psycopg2 and matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev \
    libfreetype6-dev libpng-dev \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt \
    && python - <<'PY'
import sys, importlib.util, subprocess
# If the wrong 'jwt' package was installed, remove it.
spec = importlib.util.find_spec("jwt")
if spec:
    try:
        import importlib.metadata as m
        # If 'jwt' distribution exists but PyJWT is also present, keep only PyJWT
        dists = {dist.metadata['Name'].lower(): dist.version for dist in m.distributions()}
        if 'jwt' in dists and 'pyjwt' in dists:
            subprocess.check_call([sys.executable, "-m", "pip", "uninstall", "-y", "jwt"])
    except Exception:
        pass
PY


# copy project
COPY . /app/

# collect static at build (safe if none yet)
RUN python manage.py collectstatic --noinput || true

# entrypoint runs migrations then gunicorn
COPY ./docker/web/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
