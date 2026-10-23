# PortableTAK federation add-on

OpenTAKServer 1.7.13 + federation (upstream PR #343 by fwromano, "Feature/federation v1":
v1 port 9000, v2 gRPC port 9001; SA, GeoChat, contacts) + PortableTAK fixes, shipped as an
add-on folder next to the sealed base (share/ots-federation/<arch>, first on PYTHONPATH).

- Rebuild the source: clone brian7704/OpenTAKServer, `git checkout -b pt-fed 1.7.13`, then
  `git am patches/*` (the PR #343 commits followed by the PortableTAK commits).
- Wheels: `pip download --no-deps --only-binary=:all: --python-version 3.12 --implementation cp
  --platform manylinux2014_{aarch64,x86_64} grpcio==1.82.1 -d wheels`.
- `build-overlay.sh` writes the add-on into the node package payload.
- `e2e/`: container test harness (two Setup-built drives + official TAK Server 5.8 in
  `takserver/`; the TAK Server .deb comes from tak.gov and is not in this repo).
