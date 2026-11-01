import java.io.File;
import java.net.*;
import java.nio.file.Files;
import java.util.Arrays;

/** Test only: send pre-built talk-group RTP packets (files in a folder) on wlan0, 20 ms apart. */
public class TgSend {
    public static void main(String[] a) throws Exception {
        File[] files = new File(a[0]).listFiles();
        Arrays.sort(files);
        int port = 38799 + 2 * Integer.parseInt(a[1]);
        MulticastSocket s = new MulticastSocket();
        s.setNetworkInterface(NetworkInterface.getByName("wlan0"));
        s.setTimeToLive(32);
        InetAddress g = InetAddress.getByName(a.length > 2 ? a[2] : "239.192.41.1");
        long start = System.nanoTime();
        int n = 0;
        for (File f : files) {
            byte[] b = Files.readAllBytes(f.toPath());
            s.send(new DatagramPacket(b, b.length, g, port));
            n++;
            long wait = start + n * 20_000_000L - System.nanoTime();
            if (wait > 0) Thread.sleep(wait / 1_000_000, (int) (wait % 1_000_000));
        }
        System.out.println("sent " + n + " packets on port " + port);
    }
}
