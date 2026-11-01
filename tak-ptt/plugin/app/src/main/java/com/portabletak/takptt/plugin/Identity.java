package com.portabletak.takptt.plugin;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;

import se.lublin.humla.net.HumlaCertificateGenerator;

/**
 * This phone's voice identity: a client certificate sent when connecting. Mumble servers
 * only let users with a certificate make channels or register. Phase 1: a certificate made
 * on the phone the first time it is needed (as the Mumla app does). Phase 2 uses the TAK
 * certificate enrolled in ATAK instead, when there is one.
 */
final class Identity {
    static final String PASSWORD = "";

    private Identity() {
    }

    /** The PKCS12 certificate in file, made first if missing. Slow the first time (RSA key). */
    static byte[] load(File file) throws Exception {
        if (!file.exists()) {
            File tmp = new File(file.getPath() + ".tmp");
            try (FileOutputStream out = new FileOutputStream(tmp)) {
                HumlaCertificateGenerator.generateCertificate(out);
            }
            if (!tmp.renameTo(file))
                throw new java.io.IOException("could not save " + file);
        }
        try (FileInputStream in = new FileInputStream(file)) {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            for (int n; (n = in.read(buf)) > 0; )
                bytes.write(buf, 0, n);
            return bytes.toByteArray();
        }
    }
}
