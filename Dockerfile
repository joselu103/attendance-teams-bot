# Keep the image runtime aligned with the project's required Python version.
ARG VCS_REF=dev
FROM python:3.14-slim

ARG VCS_REF
LABEL org.opencontainers.image.revision=${VCS_REF}

# Copy a pinned uv executable rather than installing it through pip at build time.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

WORKDIR /app

ENV APP_VERSION=${VCS_REF} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

# Install production dependencies first so this Docker layer is reused when only
# application code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Install the application after its source and package metadata are present.
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

# Do not run the public web process as root.
RUN useradd --create-home --shell /usr/sbin/nologin bot
USER bot

# App Runner will be configured to route traffic to this port.
EXPOSE 8080

# The existing local entry point binds to 127.0.0.1, which is correct locally
# but inaccessible to a container platform. Bind only inside the container;
# App Runner controls the public HTTPS boundary.
CMD ["uvicorn", "attendance_teams_bot.asgi:app", "--host", "0.0.0.0", "--port", "8080"]