package se.lublin.humla.audio.javacpp;

import java.io.File;

/**
 * Loads Humla's native codec libraries. Inside an ATAK plugin the plugin's libraries are
 * not on the host app's library path, so the plugin sets {@link #directory} (its
 * nativeLibraryDir) and each library is loaded by absolute path, as ATAK's plugin
 * template does. Elsewhere it falls back to System.loadLibrary.
 * (TAK PTT change to Humla.)
 */
public final class NativeLibs {
    public static volatile String directory;

    private NativeLibs() {
    }

    public static synchronized void load(String name) {
        String dir = directory;
        if (dir != null) {
            File lib = new File(dir, System.mapLibraryName(name));
            if (lib.exists()) {
                System.load(lib.getAbsolutePath());
                return;
            }
        }
        System.loadLibrary(name);
    }
}
