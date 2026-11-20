FROM debian:bookworm-slim

ARG BATTERIES=""
ENV DEBIAN_FRONTEND=noninteractive \
    UNIENV_HOME=/root/.unienv \
    PATH="/root/.unienv/bin:/root/.local/bin:${PATH}" \
    BASH_ENV=/root/.unienv/env.sh

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/unienv-src
COPY src ./src
COPY recipes ./recipes
COPY templates ./templates
COPY shims ./shims
COPY install.sh ./install.sh

RUN apt-get update \
    && if [ -n "$BATTERIES" ]; then \
         ./install.sh --yes --all-agents --batteries "$BATTERIES"; \
       else \
         ./install.sh --yes --all-agents; \
       fi \
    && rm -rf /var/lib/apt/lists/* /opt/unienv-src

WORKDIR /workspace
CMD ["/bin/bash", "-l"]
