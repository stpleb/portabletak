# Clean ARM64 builder

Build from the verified Ubuntu base archive and official Ubuntu/PyPI packages. Do not copy an existing PortableTAK rootfs or data tree.

`isolate.py` requires root Linux and a new marked development rootfs. It creates private PID, mount, IPC, and hostname namespaces. Build mode shares the host's outbound network; runtime testing uses a private network. It supplies minimal character devices and a fresh `/proc`, `/run`, and `/tmp`. Package scripts cannot start services because `policy-rc.d` denies them. Exiting PID 1 kills any remaining namespace processes.

For Node01, use `/tmp/portabletak-cleanbase-20260928` in RAM. **Do not use the ASGARD USB**, which disconnected during the first build attempt and is now unmounted.

`download_proxy.py` is a temporary Mac loopback proxy restricted to Ubuntu ports and PyPI download hosts. Its SSH reverse forward binds only to Node01's loopback. Ubuntu HTTP proxy requests are fetched over HTTPS on the Mac; apt verifies signed metadata and package checksums. HTTPS requests use CONNECT and end-to-end certificate verification. Close both processes when downloads finish. This is a build aid, not an installed host service.

`install-clean.sh` installs the clean runtime and records Ubuntu/Python package versions plus pip's download report. The compiler is required for the ARM64 unishox2 dependency. The resulting build still needs sanitization and verification.

`patch-ots.py` accepts only the reviewed upstream OTS source hashes. It generates the generic CA module and supplies configured RabbitMQ credentials to Socket.IO. It does not patch ASGARD.

`finalize-clean.py` removes generated installation state only from a marked temporary build. Then run `clean_base.audit_clean_tree`, create a content seal, and verify against the seal's independently saved hash. The build is not distributable until the complete audit passes.

`tests/test_clean_runtime_native.py` is an isolated runtime integration test. It is not production provisioning orchestration and does not mark a cartridge provisioned. MediaMTX and physical/client acceptance are explicitly outside this test.
