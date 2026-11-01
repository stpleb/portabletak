using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using Concentus.Structs;

namespace TakPtt.Voice
{
    /// <summary>Sound in and out, 48 kHz mono, in 20 ms frames.</summary>
    internal interface IAudio
    {
        IMic OpenMic();

        ISpeaker OpenSpeaker();
    }

    internal interface IMic : IDisposable
    {
        /// <summary>Fills the frame, waiting for sound. False when the microphone stopped.</summary>
        bool Read(short[] frame);
    }

    internal interface ISpeaker : IDisposable
    {
        /// <summary>Plays one frame, returning once there is room for the next (this paces the mixer).</summary>
        void Write(short[] frame);
    }

    /// <summary>Saved audio to mix in (a history clip).</summary>
    internal interface IClipSource
    {
        /// <summary>True while it has sound to give.</summary>
        bool Active { get; }

        /// <summary>Fills the frame; false when there is nothing to play right now.</summary>
        bool Next(short[] frame);
    }

    /// <summary>
    /// Radio tones, all made in code (no recorded audio): each is a list of (frequency Hz, milliseconds)
    /// steps, frequency 0 being silence, with short fades so they do not click. The same tones as
    /// the ATAK plugin.
    /// </summary>
    internal static class Tones
    {
        private const int Rate = 48000;

        public static readonly int[][] Permit = { new[] { 880, 55 }, new[] { 0, 25 }, new[] { 1320, 70 } };
        public static readonly int[][] Deny = { new[] { 330, 120 }, new[] { 0, 40 }, new[] { 330, 120 } };
        public static readonly int[][] NoChannel = { new[] { 520, 90 }, new[] { 0, 50 }, new[] { 440, 90 }, new[] { 0, 50 }, new[] { 370, 120 } };
        public static readonly int[][] Warning = { new[] { 1600, 40 }, new[] { 0, 60 }, new[] { 1600, 40 } };
        public static readonly int[][] Cutoff = { new[] { 900, 90 }, new[] { 700, 90 }, new[] { 500, 160 } };
        public static readonly int[][] Interrupt = { new[] { 700, 70 }, new[] { 0, 30 }, new[] { 700, 70 }, new[] { 0, 30 }, new[] { 700, 70 } };

        public static int LengthMs(int[][] steps) { return steps.Sum(s => s[1]); }

        public static short[] Render(int[][] steps)
        {
            var result = new short[Rate * LengthMs(steps) / 1000];
            int i = 0;
            double phase = 0;
            int fade = Rate / 400;                                  // 2.5 ms
            foreach (var s in steps)
            {
                int n = Rate * s[1] / 1000;
                for (int k = 0; k < n && i < result.Length; k++, i++)
                {
                    if (s[0] == 0) continue;
                    double env = Math.Min(1.0, Math.Min(k, n - 1 - k) / (double)fade);
                    phase += 2 * Math.PI * s[0] / Rate;
                    result[i] = (short)(Math.Sin(phase) * 0.35 * 32767 * env);
                }
            }
            return result;
        }
    }

    /// <summary>
    /// Plays everyone at once (full duplex, like the phones): each talker has an Opus decoder and a
    /// short queue, and a thread mixes the queues and any tone into the speaker every 20 ms. A talker
    /// starts playing once 40 ms is queued, to ride out network jitter, and the queue is kept to
    /// 200 ms so a stall does not build up delay.
    /// </summary>
    internal sealed class Mixer
    {
        public const int Rate = 48000, Frame = 960;
        private const int StartFrames = 2, MaxFrames = 10, MaxPacket = 5760;
        private const int IdleMs = 5000;                            // keep the sound device open this long after traffic

        private sealed class Talker
        {
            public readonly OpusDecoder Decoder = new OpusDecoder(Rate, 1);
            public readonly Queue<short[]> Frames = new Queue<short[]>();
            public bool Playing, Ended;
        }

        private readonly IAudio _audio;
        private readonly object _lock = new object();
        private readonly Dictionary<int, Talker> _talkers = new Dictionary<int, Talker>();
        private readonly Queue<short[]> _tone = new Queue<short[]>();
        private bool _running;

        public Mixer(IAudio audio) { _audio = audio; }

        /// <summary>The last speaker problem, or null.</summary>
        public volatile string Error;

        /// <summary>Set once; Kick after it starts or resumes.</summary>
        public volatile IClipSource Clip;

        /// <summary>Start the speaker if something waits to play.</summary>
        public void Kick()
        {
            lock (_lock)
            {
                if (!Idle()) Wake();
            }
        }

        public void Add(int session, byte[] opus, bool last)
        {
            lock (_lock)
            {
                try
                {
                    Talker t;
                    if (!_talkers.TryGetValue(session, out t)) _talkers[session] = t = new Talker();
                    if (opus.Length > 0)
                    {
                        // Any Opus length is legal (up to 120 ms): cut it into our 20 ms frames.
                        var pcm = new short[MaxPacket];
                        int n = t.Decoder.Decode(opus, 0, opus.Length, pcm, 0, MaxPacket, false);
                        for (int at = 0; at < n; at += Frame)
                        {
                            var f = new short[Frame];
                            Array.Copy(pcm, at, f, 0, Math.Min(Frame, n - at));
                            t.Frames.Enqueue(f);
                        }
                        while (t.Frames.Count > MaxFrames) t.Frames.Dequeue();
                    }
                    if (last) t.Ended = true;
                }
                catch (Exception)
                {
                    _talkers.Remove(session);                       // a bad packet: the next one starts clean
                }
                Wake();
            }
        }

        /// <summary>Queue a tone and return how long it plays.</summary>
        public int Play(int[][] steps)
        {
            var pcm = Tones.Render(steps);
            lock (_lock)
            {
                for (int i = 0; i < pcm.Length; i += Frame)
                {
                    var f = new short[Frame];
                    Array.Copy(pcm, i, f, 0, Math.Min(Frame, pcm.Length - i));
                    _tone.Enqueue(f);
                }
                Wake();
            }
            return Tones.LengthMs(steps);
        }

        public void Stop()
        {
            lock (_lock)
            {
                _running = false;
                _talkers.Clear();
                _tone.Clear();
            }
        }

        private void Wake()
        {
            if (_running) return;
            _running = true;
            new Thread(Run) { IsBackground = true, Name = "takptt-mixer" }.Start();
        }

        private void Run()
        {
            try
            {
                using (var speaker = _audio.OpenSpeaker())
                {
                    Error = null;
                    var mix = new short[Frame];
                    int lastSound = Environment.TickCount;
                    while (Volatile.Read(ref _running))
                    {
                        if (MixFrame(mix)) lastSound = Environment.TickCount;
                        else if (Environment.TickCount - lastSound > IdleMs && Idle()) break;
                        speaker.Write(mix);
                    }
                }
            }
            catch (Exception e)
            {
                Error = e.Message;
                lock (_lock)
                {
                    _talkers.Clear();
                    _tone.Clear();
                }
            }
            lock (_lock)
            {
                _running = false;
                if (!Idle()) Wake();
            }
        }

        private bool Idle()
        {
            var clip = Clip;
            lock (_lock) return _tone.Count == 0 && _talkers.Values.All(t => t.Frames.Count == 0) && (clip == null || !clip.Active);
        }

        /// <summary>One frame of everything queued; true when there was any sound.</summary>
        private bool MixFrame(short[] mix)
        {
            var sum = new int[mix.Length];
            bool any = false;
            lock (_lock)
            {
                if (_tone.Count > 0)
                {
                    any = true;
                    var t = _tone.Dequeue();
                    for (int i = 0; i < mix.Length; i++) sum[i] += t[i];
                }
                foreach (var pair in _talkers.ToList())
                {
                    var talker = pair.Value;
                    if (!talker.Playing && (talker.Frames.Count >= StartFrames || talker.Ended)) talker.Playing = true;
                    if (talker.Playing && talker.Frames.Count > 0)
                    {
                        any = true;
                        var f = talker.Frames.Dequeue();
                        for (int i = 0; i < mix.Length; i++) sum[i] += f[i];
                    }
                    else if (talker.Ended) _talkers.Remove(pair.Key);
                    else talker.Playing = false;                    // ran dry: build up the 40 ms again
                }
            }
            var clip = Clip;
            var saved = new short[mix.Length];
            if (clip != null && clip.Next(saved))
            {
                any = true;
                for (int i = 0; i < mix.Length; i++) sum[i] += saved[i];
            }
            for (int i = 0; i < mix.Length; i++)
                mix[i] = (short)Math.Max(short.MinValue, Math.Min(short.MaxValue, sum[i]));
            return any;
        }
    }
}
