# ---------- build stage ----------
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgeos-dev libproj-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

RUN pip install --no-cache-dir --prefix=/install .

# ---------- runtime stage ----------
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgeos-dev libproj-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN python -c "from maptoposter.font_management import load_fonts; f = load_fonts(); assert f, 'Bundled fonts missing'"

RUN useradd --create-home maptoposter
USER maptoposter
WORKDIR /home/maptoposter

HEALTHCHECK --interval=60s --timeout=5s --retries=2 \
    CMD maptoposter-cli --help > /dev/null 2>&1 || exit 1

ENTRYPOINT ["maptoposter-cli"]
