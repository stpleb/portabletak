package com.portabletak.takptt.takx;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

import javax.sound.sampled.AudioFormat;
import javax.sound.sampled.AudioSystem;
import javax.sound.sampled.DataLine;
import javax.sound.sampled.LineUnavailableException;
import javax.sound.sampled.SourceDataLine;
import javax.sound.sampled.TargetDataLine;

/** Sound in and out, 48 kHz mono, in 20 ms frames. The default sound devices unless a test swaps them. */
interface Audio {
    int RATE = 48000, FRAME = 960;

    interface Mic extends AutoCloseable {
        /** Fills the frame, waiting for sound. False when the microphone stopped. */
        boolean read(short[] frame) throws Exception;

        @Override void close();
    }

    interface Speaker extends AutoCloseable {
        /** Plays one frame, returning once there is room for the next (this paces the mixer). */
        void write(short[] frame) throws Exception;

        @Override void close();
    }

    Mic openMic() throws Exception;

    Speaker openSpeaker() throws Exception;

    /** The computer's default microphone and speakers. */
    final class Desktop implements Audio {
        private static final AudioFormat FORMAT = new AudioFormat(RATE, 16, 1, true, false);
        private static final int BYTES = FRAME * 2;

        @Override
        public Mic openMic() throws LineUnavailableException {
            final TargetDataLine line = (TargetDataLine) AudioSystem.getLine(new DataLine.Info(TargetDataLine.class, FORMAT));
            line.open(FORMAT, BYTES * 4);
            line.start();
            final byte[] buf = new byte[BYTES];
            return new Mic() {
                @Override
                public boolean read(short[] frame) {
                    int n = 0;
                    while (n < BYTES) {
                        int r = line.read(buf, n, BYTES - n);
                        if (r <= 0) return false;
                        n += r;
                    }
                    ByteBuffer.wrap(buf).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(frame);
                    return true;
                }

                @Override
                public void close() {
                    line.stop();
                    line.close();
                }
            };
        }

        @Override
        public Speaker openSpeaker() throws LineUnavailableException {
            final SourceDataLine line = (SourceDataLine) AudioSystem.getLine(new DataLine.Info(SourceDataLine.class, FORMAT));
            line.open(FORMAT, BYTES * 5);                   // 100 ms
            line.start();
            final byte[] buf = new byte[BYTES];
            return new Speaker() {
                @Override
                public void write(short[] frame) {
                    ByteBuffer.wrap(buf).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().put(frame);
                    line.write(buf, 0, BYTES);
                }

                @Override
                public void close() {
                    line.stop();
                    line.close();
                }
            };
        }
    }
}
