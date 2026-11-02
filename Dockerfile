# ---------- Stage 1: builder ----------
# Has the tools needed to download and prepare the packages. None of this ships in the final image.
FROM python:3.13-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---------- Stage 2: runtime (the image that gets deployed) ----------
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# Install only from the prepared wheels, and nothing else.
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

# Run as a normal user, not root.
RUN useradd --create-home --uid 10001 kitchen
COPY app ./app
USER kitchen

EXPOSE 8000
# Layer 1 health check: is the process alive? (Python is already here, so no curl is needed.)
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------- Stage 3: test (only used by the tests service) ----------
FROM runtime AS test
# The app folder is not writable for the normal user, so do not try to write pytest's cache there.
ENV PYTEST_ADDOPTS="-p no:cacheprovider"
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pytest.ini ./pytest.ini
COPY tests ./tests
USER kitchen
CMD ["pytest", "-v"]
