package com.portabletak.takptt.plugin;

import com.atakmap.comms.NetConnectString;
import com.atakmap.comms.TAKServer;
import com.atakmap.comms.TAKServerListener;
import com.atakmap.net.AtakAuthenticationCredentials;
import com.atakmap.net.AtakAuthenticationDatabase;
import com.atakmap.net.AtakCertificateDatabase;
import com.atakmap.net.AtakCertificateDatabaseIFace;

import org.spongycastle.jce.provider.BouncyCastleProvider;

import java.io.ByteArrayInputStream;
import java.security.KeyStore;
import java.security.cert.X509Certificate;
import java.util.Enumeration;
import java.util.List;

/**
 * Where and how to sign in to voice, taken from ATAK's TAK server connection: the voice server
 * runs on the TAK server's address (port 64738), the TAK certificate ATAK enrolled with that
 * server is the voice identity, and the server's certificate must come from the same
 * certificate authority ATAK trusts for that TAK server.
 */
final class TakVoice {
    static final int PORT = 64738;

    final String serverName;
    final String host;
    final byte[] certificate;           // PKCS12, the TAK client certificate
    final String certificatePassword;
    final String user;                  // the certificate's common name (the TAK user name)
    final List<X509Certificate> trusted;

    private TakVoice(String serverName, String host, byte[] certificate, String certificatePassword,
                     String user, List<X509Certificate> trusted) {
        this.serverName = serverName;
        this.host = host;
        this.certificate = certificate;
        this.certificatePassword = certificatePassword;
        this.user = user;
        this.trusted = trusted;
    }

    /** Thrown with a message for the user when voice cannot be set up from the TAK connection. */
    static final class SetupException extends Exception {
        SetupException(String message) {
            super(message);
        }
    }

    /** The TAK server ATAK is connected to (or, when none is connected, the first one set up). */
    static TakVoice fromAtak() throws SetupException {
        TAKServerListener listener = TAKServerListener.getInstance();
        TAKServer[] servers = listener != null ? listener.getConnectedServers() : null;
        if (servers == null || servers.length == 0)
            servers = listener != null ? listener.getServers() : null;
        if (servers == null || servers.length == 0)
            throw new SetupException("Connect ATAK to your PortableTAK server first");
        TAKServer server = servers[0];
        NetConnectString ncs = NetConnectString.fromString(server.getConnectString());
        if (ncs == null)
            throw new SetupException("Could not read the TAK server's address");
        String host = ncs.getHost();
        int port = ncs.getPort();
        String name = server.getDescription() != null && !server.getDescription().isEmpty()
                ? server.getDescription() : host;

        byte[] cert = certificate(AtakCertificateDatabaseIFace.TYPE_CLIENT_CERTIFICATE, host, port);
        String certPassword = password(AtakAuthenticationCredentials.TYPE_clientPassword, host);
        if (cert == null || certPassword == null)
            throw new SetupException("ATAK has no certificate for " + name + " yet. Connect to it in ATAK first.");
        String user = commonName(cert, certPassword);

        byte[] trust = certificate(AtakCertificateDatabaseIFace.TYPE_TRUST_STORE_CA, host, port);
        String trustPassword = password(AtakAuthenticationCredentials.TYPE_caPassword, host);
        List<X509Certificate> trusted = trust != null
                ? AtakCertificateDatabase.loadCertificate(trust, trustPassword) : null;
        if (trusted == null || trusted.isEmpty())
            throw new SetupException("ATAK has no trust store for " + name);
        return new TakVoice(name, host, cert, certPassword, user, trusted);
    }

    private static byte[] certificate(String type, String host, int port) {
        byte[] b = AtakCertificateDatabase.getCertificateForServerAndPort(type, host, port);
        if (b == null)
            b = AtakCertificateDatabase.getCertificateForServer(type, host);
        if (b == null)
            b = AtakCertificateDatabase.getCertificate(type);
        return b;
    }

    private static String password(String type, String host) {
        AtakAuthenticationCredentials c = AtakAuthenticationDatabase.getCredentials(type, host);
        if (c == null || c.password == null || c.password.isEmpty())
            c = AtakAuthenticationDatabase.getCredentials(type);
        return c != null && c.password != null && !c.password.isEmpty() ? c.password : null;
    }

    /** The user name in the TAK certificate; also checks the certificate and password work. */
    private static String commonName(byte[] p12, String password) throws SetupException {
        try {
            KeyStore ks = KeyStore.getInstance("PKCS12", new BouncyCastleProvider());
            ks.load(new ByteArrayInputStream(p12), password.toCharArray());
            for (Enumeration<String> e = ks.aliases(); e.hasMoreElements(); ) {
                String alias = e.nextElement();
                if (!ks.isKeyEntry(alias)) continue;
                X509Certificate c = (X509Certificate) ks.getCertificate(alias);
                for (String part : c.getSubjectX500Principal().getName().split(","))
                    if (part.trim().startsWith("CN="))
                        return part.trim().substring(3);
                return c.getSubjectX500Principal().getName();
            }
        } catch (Exception e) {
            throw new SetupException("Could not open the TAK certificate: " + e.getMessage());
        }
        throw new SetupException("The TAK certificate has no key");
    }
}
