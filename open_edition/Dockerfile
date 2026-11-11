# Proofpane Community, the hosted open edition. Caddy terminates TLS itself, offering the hybrid post-quantum key
# exchange X25519MLKEM768 (Fly's edge documents none, so Fly passes TCP through), and proxies to uvicorn on localhost.
# Built from the repository root, since it copies the open packages from oss/:
#     docker build -f open_edition/Dockerfile .
FROM caddy:2.11-alpine AS caddy

# The ADE app (ui/, React and Vite) is built here; only its output reaches the image, served at /app/.
FROM node:22-alpine AS ui
WORKDIR /src/ui
COPY open_edition/ui/package.json open_edition/ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY open_edition/ui/ ./
# the page's build writes Proofpane Community's own LICENSE, LICENSE-APACHE-2.0 and NOTICE beside it (ui/licenses.ts),
# from the folder above
COPY open_edition/LICENSE open_edition/LICENSE-APACHE-2.0 open_edition/NOTICE /src/
RUN npm run build

FROM python:3.12-slim
# glibc keeps an arena per thread up to eight per core: with scrypt's 16 MiB per password check, 40 threads' checks
# kept 541 MiB on four cores, and 61 MiB with two arenas (auth.py)
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 \
    OPEN_EDITION_OSS_ROOT=/app/oss OPEN_EDITION_DATA=/data
COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
COPY open_edition/requirements.txt /app/open_edition/requirements.txt
RUN pip install --no-cache-dir -r /app/open_edition/requirements.txt
COPY oss/core /app/oss/core
COPY oss/verify /app/oss/verify
COPY oss/verify-pr /app/oss/verify-pr
COPY oss/sign /app/oss/sign
COPY open_edition /app/open_edition
COPY --from=ui /src/web/app /app/open_edition/web/app
RUN rm -rf /app/open_edition/tests /app/open_edition/ui /app/oss/*/tests && chmod +x /app/open_edition/start.sh /app/open_edition/proofpane-open \
    && ln -s /app/open_edition/proofpane-open /usr/local/bin/proofpane-open
# The release and the commit the image is built from, as GET /api/health says them (proofpane_open/build.py): build
# arguments the release's deploy gives (fly.toml), never read from git here, since the build context holds no
# repository. Either one not given is "unknown"; one given and not of its shape stops the build. Declared after
# everything else, so a new commit builds this one layer again.
ARG OPEN_EDITION_RELEASE=unknown
ARG OPEN_EDITION_COMMIT=unknown
RUN python /app/open_edition/proofpane_open/build.py "$OPEN_EDITION_RELEASE" "$OPEN_EDITION_COMMIT"
WORKDIR /app/open_edition
EXPOSE 80 443
CMD ["/app/open_edition/start.sh"]
