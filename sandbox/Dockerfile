FROM debian:bookworm-slim

ARG AGENT_DESKTOP_VERSION=1.3.1
ARG INSTALL_CHROMIUM=0

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xvfb \
      openbox \
      dbus \
      x11-utils \
      at-spi2-core \
      libatk-bridge2.0-0 \
      curl \
      ca-certificates \
      mousepad \
      x11vnc \
      novnc \
      websockify \
      procps \
 && if [ "${INSTALL_CHROMIUM}" = "1" ]; then \
      apt-get install -y --no-install-recommends chromium; \
    fi \
 && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) target_arch="aarch64" ;; \
      amd64) target_arch="x86_64" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    tarball="agent-desktop-v${AGENT_DESKTOP_VERSION}-${target_arch}-unknown-linux-gnu.tar.gz"; \
    base_url="https://github.com/vercel-labs/agent-desktop/releases/download/v${AGENT_DESKTOP_VERSION}"; \
    mkdir -p /tmp/agent-desktop-dl; \
    cd /tmp/agent-desktop-dl; \
    curl -fsSL -o "${tarball}" "${base_url}/${tarball}"; \
    curl -fsSL -o checksums.txt "${base_url}/checksums.txt"; \
    awk -v f="${tarball}" '$2 == f { print; found=1 } END { exit !found }' checksums.txt | sha256sum -c -; \
    tar -xzf "${tarball}" -C /usr/local/bin agent-desktop agent-desktop-linux-clipboard-owner; \
    chmod 0755 /usr/local/bin/agent-desktop /usr/local/bin/agent-desktop-linux-clipboard-owner; \
    rm -rf /tmp/agent-desktop-dl

RUN useradd --create-home --shell /bin/bash agent \
 && mkdir -p /tmp/.X11-unix \
 && chmod 1777 /tmp/.X11-unix

COPY agent-desktop-run /usr/local/bin/agent-desktop-run
RUN chmod 0755 /usr/local/bin/agent-desktop-run

EXPOSE 6080

USER agent
WORKDIR /home/agent
CMD ["sleep", "infinity"]
