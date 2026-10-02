#!/bin/sh
set -eu
export https_proxy=http://127.0.0.1:18765
export HTTP_PROXY="$https_proxy" HTTPS_PROXY="$https_proxy"
export DEBIAN_FRONTEND=noninteractive
cat > /usr/sbin/policy-rc.d <<'EOF'
#!/bin/sh
exit 101
EOF
chmod 755 /usr/sbin/policy-rc.d
mkdir -p /etc/postgresql-common
printf 'create_main_cluster = false\n' > /etc/postgresql-common/createcluster.conf
# Official Ubuntu ports archive; package signatures are verified by apt.
cat > /etc/apt/sources.list.d/ubuntu.sources <<'EOF'
Types: deb
URIs: http://ports.ubuntu.com/ubuntu-ports/
Suites: noble noble-updates noble-security
Components: main universe
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
apt-get -o Acquire::http::Proxy="$https_proxy" -o APT::Update::Error-Mode=any update
apt-get -o Acquire::http::Proxy="$https_proxy" install -y --no-install-recommends ca-certificates build-essential python3-dev python3 python3-venv postgresql-16 postgresql-16-postgis-3 rabbitmq-server nginx openssl tzdata iproute2 curl libmagic1
mkdir -p /asgard/opentak
python3 -m venv /asgard/opentak/venv
/asgard/opentak/venv/bin/python -m pip install --no-cache-dir --disable-pip-version-check --report /build-python-report.json 'opentakserver==1.7.13'
/asgard/opentak/venv/bin/python -m pip freeze --all > /build-python-packages.txt
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' > /build-apt-packages.txt
