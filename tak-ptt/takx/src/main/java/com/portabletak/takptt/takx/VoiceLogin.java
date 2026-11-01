package com.portabletak.takptt.takx;

import org.bouncycastle.asn1.pkcs.ContentInfo;
import org.bouncycastle.asn1.pkcs.PKCSObjectIdentifiers;
import org.bouncycastle.cert.X509CertificateHolder;
import org.bouncycastle.jce.provider.BouncyCastleProvider;
import org.bouncycastle.operator.InputDecryptorProvider;
import org.bouncycastle.pkcs.PKCS12PfxPdu;
import org.bouncycastle.pkcs.PKCS12SafeBag;
import org.bouncycastle.pkcs.PKCS12SafeBagFactory;
import org.bouncycastle.pkcs.jcajce.JcePKCSPBEInputDecryptorProviderBuilder;

import java.io.ByteArrayInputStream;
import java.security.KeyStore;
import java.security.Provider;
import java.security.SecureRandom;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;

/**
 * Where and how to sign in to voice, taken from a TAK server connection: the voice server runs on
 * the TAK server's address (port 64738), the TAK certificate enrolled with that server is the
 * voice identity, and the voice server's certificate must come from the same certificate authority
 * trusted for that TAK server. No address, account or password to set up.
 *
 * The stores are read with BouncyCastle, as on the phones: Java's own PKCS12 reader skips the CA
 * certificates in a store made with OpenSSL, which is how TAK trust stores are usually made.
 */
final class VoiceLogin {
    static final int PORT = 64738;

    final String serverName, host, user;
    final int port;
    final SSLContext tls;

    VoiceLogin(String serverName, String host, int port, String user, SSLContext tls) {
        this.serverName = serverName;
        this.host = host;
        this.port = port;
        this.user = user;
        this.tls = tls;
    }

    /** Thrown with a message for the user when voice cannot be set up from the TAK connection. */
    static final class SetupException extends Exception {
        SetupException(String message) {
            super(message);
        }
    }

    static VoiceLogin create(String serverName, String host, int port, byte[] certificate, String certificatePassword,
                             byte[] trust, String trustPassword) throws SetupException {
        if (host == null || host.isEmpty())
            throw new SetupException("Could not read the TAK server's address");
        if (certificate == null || certificate.length == 0)
            throw new SetupException(serverName + " has no client certificate. TAK PTT signs in with the certificate, so enroll for one in TAKX first.");
        if (trust == null || trust.length == 0)
            throw new SetupException("TAKX has no trust store for " + serverName);
        Provider bc = new BouncyCastleProvider();
        KeyStore client = load(bc, certificate, certificatePassword, "the TAK certificate");
        List<Certificate> cas = certificates(trust, trustPassword);
        if (cas.isEmpty())
            throw new SetupException("The trust store for " + serverName + " is empty");
        String user = commonName(client);
        try {
            KeyStore trusted = KeyStore.getInstance(KeyStore.getDefaultType());
            trusted.load(null, null);
            int count = 0;
            for (Certificate c : cas)
                trusted.setCertificateEntry("ca-" + count++, c);
            KeyManagerFactory kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
            kmf.init(client, password(certificatePassword));
            TrustManagerFactory tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
            tmf.init(trusted);
            SSLContext tls = SSLContext.getInstance("TLS");
            tls.init(kmf.getKeyManagers(), tmf.getTrustManagers(), new SecureRandom());
            return new VoiceLogin(serverName, host, port, user, tls);
        } catch (Exception e) {
            throw new SetupException("Could not set up the certificates: " + e.getMessage());
        }
    }

    private static char[] password(String p) { return p == null ? new char[0] : p.toCharArray(); }

    private static KeyStore load(Provider bc, byte[] store, String pass, String what) throws SetupException {
        try {
            KeyStore ks = KeyStore.getInstance("PKCS12", bc);
            ks.load(new ByteArrayInputStream(store), password(pass));
            return ks;
        } catch (Exception e) {
            throw new SetupException("Could not open " + what + ": " + e.getMessage());
        }
    }

    /**
     * Every certificate in a trust store. The PKCS12 bags are read directly: OpenSSL leaves the CA
     * certificates in a store without names, and a KeyStore then shows none of them.
     */
    private static List<Certificate> certificates(byte[] store, String pass) throws SetupException {
        List<Certificate> out = new ArrayList<>();
        try {
            CertificateFactory cf = CertificateFactory.getInstance("X.509");
            if (store[0] != 0x30 || store.length < 2 || isCertificate(cf, store)) {     // PEM, or a single DER certificate
                out.addAll(cf.generateCertificates(new ByteArrayInputStream(store)));
                return out;
            }
            InputDecryptorProvider key = new JcePKCSPBEInputDecryptorProviderBuilder().setProvider(new BouncyCastleProvider())
                    .build(password(pass));
            for (ContentInfo info : new PKCS12PfxPdu(store).getContentInfos()) {
                PKCS12SafeBagFactory bags = info.getContentType().equals(PKCSObjectIdentifiers.encryptedData)
                        ? new PKCS12SafeBagFactory(info, key) : new PKCS12SafeBagFactory(info);
                for (PKCS12SafeBag bag : bags.getSafeBags())
                    if (bag.getBagValue() instanceof X509CertificateHolder)
                        out.add(cf.generateCertificate(new ByteArrayInputStream(((X509CertificateHolder) bag.getBagValue()).getEncoded())));
            }
            return out;
        } catch (Exception e) {
            throw new SetupException("Could not open the TAK trust store: " + e.getMessage());
        }
    }

    private static boolean isCertificate(CertificateFactory cf, byte[] der) {
        try {
            cf.generateCertificate(new ByteArrayInputStream(der));
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /** The user name in the TAK certificate. */
    private static String commonName(KeyStore ks) throws SetupException {
        try {
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
            throw new SetupException("Could not read the TAK certificate: " + e.getMessage());
        }
        throw new SetupException("The TAK certificate has no key");
    }
}
