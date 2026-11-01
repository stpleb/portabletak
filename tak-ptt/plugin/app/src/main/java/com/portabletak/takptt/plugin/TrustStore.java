package com.portabletak.takptt.plugin;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.security.KeyStore;
import java.security.cert.X509Certificate;

/**
 * Server certificates this phone trusts for voice, kept in BKS files. With a TAK server (the
 * normal case) the store holds that server's certificate authorities, copied from ATAK. With a
 * test override and no TAK server, a server's certificate is trusted the first time the user
 * connects to it, as the Mumla app does.
 */
final class TrustStore {
    static final String PASSWORD = "takptt";

    private TrustStore() {
    }

    static void add(File file, X509Certificate cert) throws Exception {
        KeyStore store = KeyStore.getInstance("BKS");
        if (file.exists()) {
            try (FileInputStream in = new FileInputStream(file)) {
                store.load(in, PASSWORD.toCharArray());
            }
        } else {
            store.load(null, null);
        }
        store.setCertificateEntry(cert.getSubjectX500Principal().getName() + "/" + cert.getSerialNumber(), cert);
        try (FileOutputStream out = new FileOutputStream(file)) {
            store.store(out, PASSWORD.toCharArray());
        }
    }

    /** Replace file with a store holding exactly these certificates. */
    static void write(File file, java.util.List<X509Certificate> certs) throws Exception {
        KeyStore store = KeyStore.getInstance("BKS");
        store.load(null, null);
        int n = 0;
        for (X509Certificate cert : certs)
            store.setCertificateEntry("ca" + (n++), cert);
        File tmp = new File(file.getPath() + ".tmp");
        try (FileOutputStream out = new FileOutputStream(tmp)) {
            store.store(out, PASSWORD.toCharArray());
        }
        if (!tmp.renameTo(file))
            throw new java.io.IOException("could not save " + file);
    }
}
