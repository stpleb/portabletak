# Test partner: official TAK Server 5.8 (from tak.gov, signature verified) on Ubuntu 24.04.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -qq update && apt-get -qq install -y curl ca-certificates gnupg lsb-release >/dev/null && \
    install -d /usr/share/postgresql-common/pgdg && \
    curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc && \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt noble-pgdg main" > /etc/apt/sources.list.d/pgdg.list && \
    apt-get -qq update
COPY takserver_5.8-RELEASE92_all.deb /tmp/
RUN apt-get -qq install -y /tmp/takserver_5.8-RELEASE92_all.deb >/tmp/install.log 2>&1 || (tail -30 /tmp/install.log; exit 1); rm /tmp/takserver_5.8-RELEASE92_all.deb
