ARG PYTHON_VERSION=3.13

FROM node:24-slim AS frontend-builder

WORKDIR /app/frontend

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates

RUN npm install -g pnpm

COPY . /app

#needed for pnpm to tell it that there is no TTY
ENV CI=true

RUN pnpm install --frozen-lockfile

RUN --mount=source=.git,target=/app/.git,type=bind \
    pnpm build

FROM python:${PYTHON_VERSION}-slim AS backend-builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# gcc, libldap and libsas are required to compile bonsai (the ldap library we use) since it doesn't have any wheels on pypi
RUN apt-get update && apt-get install -y --no-install-recommends git gcc libldap2-dev libsasl2-dev

WORKDIR /app

COPY lib ./lib/

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/uv.lock,target=backend/uv.lock \
    --mount=type=bind,source=backend/pyproject.toml,target=backend/pyproject.toml \
    uv sync --locked --no-install-project --no-editable --compile-bytecode --project backend

ADD . /app

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=.git,target=.git \
    uv sync --locked --no-editable --compile-bytecode --project backend

FROM python:${PYTHON_VERSION}-slim

# curl to be able to perform health check
RUN apt-get update && apt-get install -y --no-install-recommends curl

# Copy licensing information
COPY ./README.md ./LICENSE.md ./COPYING.md /app/

# Copy the environment, but not the source code
COPY --from=backend-builder --chown=app:app /app/backend/.venv /app/backend/.venv

COPY --from=frontend-builder /app/frontend/build /app/frontend

CMD ["/app/backend/.venv/bin/project_W", "--root_static_files", "/app/frontend"]
