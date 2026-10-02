# Linux tools the PortableTAK setup needs (runs natively on Apple Silicon).
FROM ubuntu:24.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
        e2fsprogs fdisk python3 iproute2 net-tools procps util-linux diffutils bsdutils \
        iputils-ping openssl ca-certificates dnsmasq-base dosfstools mtools python3-qrcode python3-pil \
    && rm -rf /var/lib/apt/lists/*
