"""Federation actions for the PortableTAK dashboard (run inside the OpenTAKServer
app context by users_worker). Needs the federation add-on (OpenTAKServer with
PR #343); without it every action answers federation_unavailable.

Groups: users without group memberships publish into "__ANON__" (as on the
official TAK Server), so sharing "everyone" means ["__ANON__"] both ways.
"""
import os
import re

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")
HOST = re.compile(r"[A-Za-z0-9.:-]{1,253}")
GROUP = re.compile(r"[A-Za-z0-9 _.-]{1,64}")
EVERYONE = ["__ANON__"]


def _fail(error):
    return {"ok": False, "error": error}


def _modules():
    try:
        from opentakserver.federation import truststore
        from opentakserver.models.Federation import Federation
    except ImportError:
        return None, None
    return truststore, Federation


def _groups(value):
    if value in (None, "everyone"):
        return list(EVERYONE)
    if not isinstance(value, list) or not all(isinstance(g, str) and GROUP.fullmatch(g) for g in value):
        raise ValueError("invalid_groups")
    return value


def our_ca_pem(app):
    with open(os.path.join(app.config.get("OTS_CA_FOLDER"), "ca.pem")) as stream:
        return stream.read()


PEM_BLOCK = re.compile(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----")


def our_identity(app, truststore):
    """This server's federation certificate (leaf only), fingerprint and name."""
    cert_path, _ = truststore._server_cert_paths(app.config)
    with open(cert_path) as stream:
        leaf = PEM_BLOCK.search(stream.read()).group(0) + "\n"
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    cert = x509.load_pem_x509_certificate(leaf.encode())
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return leaf, truststore.fingerprint_from_pem(leaf.encode()), (names[0].value if names else "")


def _check_issued_by(cert_pem, ca_pem):
    """True when cert_pem is signed by ca_pem (RSA or EC)."""
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    ca = x509.load_pem_x509_certificate(ca_pem.encode())
    key = ca.public_key()
    try:
        if isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(cert.signature_hash_algorithm))
        else:
            key.verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), cert.signature_hash_algorithm)
        return True
    except Exception:
        return False


def _unique_name(db, Federation, wanted):
    name, n = wanted, 2
    while db.session.query(Federation).filter_by(name=name).first() is not None:
        name, n = "%s (%d)" % (wanted[:58], n), n + 1
    return name


def handle(app, db, request):
    truststore, Federation = _modules()
    if Federation is None:
        return _fail("federation_unavailable")
    action = request.get("action", "")

    if action == "federation_status":
        rows = db.session.query(Federation).order_by(Federation.name).all()
        leaf, fingerprint, common_name = our_identity(app, truststore)
        return {"ok": True, "federates": [row.to_json() for row in rows],
                "cas": truststore.list_cas(app.config), "our_ca": our_ca_pem(app),
                "our_cert": leaf, "our_fingerprint": fingerprint, "our_common_name": common_name}

    if action == "federation_save":
        name = str(request.get("name") or "").strip()
        if not NAME.fullmatch(name):
            return _fail("invalid_name")
        address = str(request.get("address") or "").strip()
        outbound = bool(request.get("outbound", True))
        if outbound and not HOST.fullmatch(address):
            return _fail("invalid_address")
        try:
            protocol = int(request.get("protocol_version", 2))
            port = int(request.get("port") or (9001 if protocol == 2 else 9000))
            inbound_groups = _groups(request.get("inbound_groups"))
            outbound_groups = _groups(request.get("outbound_groups"))
            # direction: both | send (we send, receive nothing) | receive (the reverse)
            direction = request.get("direction")
            if direction == "send":
                inbound_groups = []
            elif direction == "receive":
                outbound_groups = []
            elif direction not in (None, "both"):
                return _fail("invalid_direction")
        except (TypeError, ValueError) as error:
            return _fail(str(error) if str(error).startswith("invalid") else "invalid_number")
        if protocol not in (1, 2) or not 1 <= port <= 65535:
            return _fail("invalid_port_or_protocol")
        row = db.session.query(Federation).filter_by(name=name).first()
        if row is None:
            row = Federation(name=name)
        row.address = address or None
        row.port = port
        row.protocol_version = protocol
        row.outbound = outbound
        row.enabled = bool(request.get("enabled", True))
        row.reconnect_interval = 30
        row.inbound_groups = inbound_groups
        row.outbound_groups = outbound_groups
        if request.get("reset_pin"):
            row.cert_fingerprint = None
            row.cert_common_name = None
        pin = str(request.get("pin") or "").lower()
        if pin:
            if not re.fullmatch(r"[0-9a-f]{64}", pin):
                return _fail("invalid_pin")
            row.cert_fingerprint = pin
            row.cert_common_name = str(request.get("pin_name") or "")[:255] or None
        db.session.add(row)
        db.session.commit()
        return {"ok": True, "federate": row.to_json()}

    if action == "federation_delete":
        row = db.session.query(Federation).filter_by(name=str(request.get("name") or "")).first()
        if row is None:
            return _fail("not_found")
        db.session.delete(row)
        db.session.commit()
        return {"ok": True}

    if action == "federation_ca_add":
        pem = str(request.get("pem") or "")
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", str(request.get("filename") or "federate-ca.pem"))[:64]
        if not filename.endswith(".pem"):
            filename += ".pem"
        if "BEGIN CERTIFICATE" not in pem:
            return _fail("not_a_certificate")
        try:
            return {"ok": True, "ca": truststore.save_ca(app.config, filename, pem.encode())}
        except ValueError as error:
            return _fail("invalid_certificate: %s" % error)

    if action == "federation_ca_delete":
        if truststore.delete_ca(app.config, str(request.get("filename") or "")):
            return {"ok": True}
        return _fail("not_found")

    if action == "federation_accept":
        # Invite redeemed by the host (secret already checked): trust the joining
        # server's CA and allow sharing with its federation certificate.
        name = str(request.get("name") or "").strip()[:60] or "PortableTAK"
        if not NAME.fullmatch(name):
            name = "PortableTAK server"
        ca_pem, cert_pem = str(request.get("ca") or ""), str(request.get("cert") or "")
        if "BEGIN CERTIFICATE" not in ca_pem or "BEGIN CERTIFICATE" not in cert_pem:
            return _fail("not_a_certificate")
        if not _check_issued_by(cert_pem, ca_pem):
            return _fail("certificate_not_from_ca")
        fingerprint = truststore.fingerprint_from_pem(cert_pem.encode())
        try:
            truststore.save_ca(app.config, re.sub(r"[^A-Za-z0-9._-]", "_", name)[:48] + "-CA.pem", ca_pem.encode())
        except ValueError as error:
            return _fail("invalid_certificate: %s" % error)
        row = db.session.query(Federation).filter_by(cert_fingerprint=fingerprint).first()
        if row is None:
            row = Federation(name=_unique_name(db, Federation, name))
            row.cert_fingerprint = fingerprint
        row.outbound = False
        row.enabled = True
        row.port = 9001
        row.protocol_version = 2
        row.inbound_groups = list(EVERYONE)
        row.outbound_groups = list(EVERYONE)
        db.session.add(row)
        db.session.commit()
        leaf, ours, _ = our_identity(app, truststore)
        return {"ok": True, "federate": row.to_json(), "our_fingerprint": ours}

    if action == "federation_fetch_ca":
        # Read a server's certificate chain from its federation port (no trust yet):
        # the dashboard shows the CA for the admin to confirm.
        import subprocess
        address = str(request.get("address") or "").strip()
        if not HOST.fullmatch(address):
            return _fail("invalid_address")
        try:
            port = int(request.get("port") or 9001)
        except (TypeError, ValueError):
            return _fail("invalid_number")
        try:
            out = subprocess.run(["openssl", "s_client", "-showcerts", "-connect", "%s:%d" % (address, port),
                                  "-servername", address, "-alpn", "h2"],
                                 input="", capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.TimeoutExpired):
            return _fail("server_unreachable")
        blocks = [b + "\n" for b in PEM_BLOCK.findall(out)]
        if not blocks:
            return _fail("no_certificate")
        from cryptography import x509
        certs = [x509.load_pem_x509_certificate(b.encode()) for b in blocks]
        ca_index = next((i for i, c in enumerate(certs) if i and c.subject == c.issuer), len(certs) - 1)
        if ca_index == 0:
            return _fail("ca_not_sent")
        ca = certs[ca_index]
        return {"ok": True, "ca": blocks[ca_index], "ca_subject": ca.subject.rfc4514_string(),
                "ca_fingerprint": truststore.fingerprint_from_pem(blocks[ca_index].encode()),
                "server_subject": certs[0].subject.rfc4514_string(),
                "server_fingerprint": truststore.fingerprint_from_pem(blocks[0].encode())}

    return _fail("invalid_action")
