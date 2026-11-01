using System;
using System.Collections.Concurrent;
using System.Threading;
using NAudio.Wave;
using TakPtt.Voice;

namespace TakPtt.WinTak
{
    /// <summary>The computer's default microphone and speakers (Windows' own wave audio, through NAudio).</summary>
    internal sealed class DesktopAudio : IAudio
    {
        private static readonly WaveFormat Format = new WaveFormat(Mixer.Rate, 16, 1);

        public IMic OpenMic() { return new Mic(); }

        public ISpeaker OpenSpeaker() { return new Speaker(); }

        private sealed class Mic : IMic
        {
            private const int WaitMs = 2000;
            private readonly WaveInEvent _in = new WaveInEvent { WaveFormat = Format, BufferMilliseconds = 20, NumberOfBuffers = 4 };
            private readonly BlockingCollection<byte[]> _blocks = new BlockingCollection<byte[]>(64);
            private byte[] _block;
            private int _at;

            public Mic()
            {
                _in.DataAvailable += (s, e) =>
                {
                    var b = new byte[e.BytesRecorded];
                    Buffer.BlockCopy(e.Buffer, 0, b, 0, b.Length);
                    _blocks.TryAdd(b);
                };
                _in.StartRecording();
            }

            public bool Read(short[] frame)
            {
                int filled = 0;
                while (filled < frame.Length)
                {
                    if (_block == null || _at >= _block.Length)
                    {
                        if (!_blocks.TryTake(out _block, WaitMs)) return false;
                        _at = 0;
                    }
                    while (filled < frame.Length && _at + 1 < _block.Length)
                    {
                        frame[filled++] = (short)(_block[_at] | (_block[_at + 1] << 8));
                        _at += 2;
                    }
                    if (_at + 1 >= _block.Length) _at = _block.Length;
                }
                return true;
            }

            public void Dispose()
            {
                _in.StopRecording();
                _in.Dispose();
            }
        }

        private sealed class Speaker : ISpeaker
        {
            private static readonly TimeSpan Queue = TimeSpan.FromMilliseconds(100);
            private readonly BufferedWaveProvider _buffer = new BufferedWaveProvider(Format)
            {
                BufferDuration = TimeSpan.FromMilliseconds(500),
                DiscardOnBufferOverflow = true
            };
            private readonly WaveOutEvent _out = new WaveOutEvent { DesiredLatency = 100, NumberOfBuffers = 3 };
            private readonly byte[] _bytes = new byte[Mixer.Frame * 2];

            public Speaker()
            {
                _out.Init(_buffer);
                _out.Play();
            }

            public void Write(short[] frame)
            {
                Buffer.BlockCopy(frame, 0, _bytes, 0, _bytes.Length);
                _buffer.AddSamples(_bytes, 0, _bytes.Length);
                while (_buffer.BufferedDuration > Queue) Thread.Sleep(5);
            }

            public void Dispose()
            {
                _out.Stop();
                _out.Dispose();
            }
        }
    }
}
