using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using Concentus.Structs;
using TakPtt.Voice;

// Runs the voice code that ships in the plugin against a real Murmur, with a made-up microphone and
// speaker, so voice can be checked without WinTAK or Windows. See tools/run-e2e.sh.
//   TakPtt.E2E HOST PORT DIR   (DIR holds alice.p12, rogue.p12, trust.p12, rogue-trust.p12)
internal static class Program
{
    internal static int _failures;

    internal sealed class MemoryPrefs : IPrefs
    {
        private readonly Dictionary<string, string> _values = new Dictionary<string, string> { { "tones", "0" } };

        public string Get(string key, string otherwise) { lock (_values) return _values.TryGetValue(key, out var v) ? v : otherwise; }

        public void Put(string key, string value) { lock (_values) _values[key] = value; }
    }

    /// <summary>A 440 Hz tone at the pace of a real microphone.</summary>
    internal sealed class FakeAudio : IAudio
    {
        public readonly List<short[]> Heard = new List<short[]>();
        public int MicFrames;

        private sealed class Mic : IMic
        {
            private readonly FakeAudio _a;
            private long _n, _next = Environment.TickCount64;

            public Mic(FakeAudio a) { _a = a; }

            public bool Read(short[] frame)
            {
                long wait = _next - Environment.TickCount64;
                if (wait > 0) Thread.Sleep((int)wait);
                _next += 20;
                for (int i = 0; i < frame.Length; i++, _n++)
                    frame[i] = (short)(9000 * Math.Sin(2 * Math.PI * 440 * _n / 48000));
                Interlocked.Increment(ref _a.MicFrames);
                return true;
            }

            public void Dispose() { }
        }

        private sealed class Speaker : ISpeaker
        {
            private readonly FakeAudio _a;

            public Speaker(FakeAudio a) { _a = a; }

            public void Write(short[] frame)
            {
                lock (_a.Heard) _a.Heard.Add((short[])frame.Clone());
                Thread.Sleep(20);
            }

            public void Dispose() { }
        }

        public IMic OpenMic() { return new Mic(this); }

        public ISpeaker OpenSpeaker() { return new Speaker(this); }

        /// <summary>Strongest stretch heard: peak and frequency in Hz.</summary>
        public (int peak, double hz) Loudest()
        {
            lock (Heard)
            {
                int peak = 0, crossings = 0, loud = 0;
                short prev = 0;
                foreach (var f in Heard)
                {
                    int p = f.Max(s => Math.Abs((int)s));
                    peak = Math.Max(peak, p);
                    if (p < 2000) continue;
                    loud++;
                    foreach (var s in f)
                    {
                        if (prev < 0 && s >= 0) crossings++;
                        prev = s;
                    }
                }
                return (peak, loud == 0 ? 0 : crossings / (loud * 0.02));
            }
        }
    }

    internal sealed class Run
    {
        public volatile VoiceState State;
        public readonly FakeAudio Audio = new FakeAudio();
        public readonly MemoryPrefs Prefs = new MemoryPrefs();
        public readonly VoiceEngine Engine;

        public Run() { Engine = new VoiceEngine(Audio, Prefs, s => State = s); }

        public bool Await(Func<VoiceState, bool> test, int ms)
        {
            long end = Environment.TickCount64 + ms;
            while (Environment.TickCount64 < end)
            {
                var s = State;
                if (s != null && test(s)) return true;
                Thread.Sleep(50);
            }
            return false;
        }
    }

    internal static void Check(string what, bool ok, string detail = "")
    {
        Console.WriteLine((ok ? "PASS " : "FAIL ") + what + (detail.Length == 0 ? "" : ": " + detail));
        if (!ok) _failures++;
    }

    private static VoiceLogin Login(string host, int port, string dir, string client, string trust)
    {
        return VoiceLogin.Create("Test TAK", host, port, File.ReadAllBytes(Path.Combine(dir, client)), "atakatak",
            File.ReadAllBytes(Path.Combine(dir, trust)), "atakatak");
    }

    private static void Packets()
    {
        bool ok = true;
        foreach (long v in new long[] { 0, 1, 127, 128, 16383, 16384, 2097151, 2097152, 268435455, 268435456, 4294967295L, 4294967296L, -1, -2, -4, -5, -300 })
        {
            var o = new MemoryStream();
            VoicePacket.PutVarint(o, v);
            int at = 0;
            ok &= VoicePacket.GetVarint(o.ToArray(), ref at) == v;
        }
        Check("voice number format round trip", ok);
        var p = VoicePacket.Decode(new byte[] { 0x80, 7, 0x81, 0x2C, 0xA0, 0x03, 1, 2, 3 });
        Check("incoming voice packet", p != null && p.Session == 7 && p.Sequence == 300 && p.Data.Length == 3 && p.Last);
        var b = VoicePacket.Encode(5, 4, new byte[] { 9, 9 }, 2, false);
        Check("outgoing voice packet", b[0] == 0x85 && b[1] == 4 && b[2] == 2 && b.Length == 5);
        Check("control message round trip", Pb.Parse(new Pb().UInt(1, 300).Str(3, "Alpha").Bool(8, true).Done()).Text(3, "") == "Alpha");
    }

    private static int Main(string[] a)
    {
        if (a[0] == "whisper") return WhisperTest.Run(a[1], a[2], a[3], a[4]);
        if (a[0] == "history") return HistoryTest.Run(a[1], int.Parse(a[2]), int.Parse(a[3]), a[4]);
        string host = a[0], dir = a[2];
        int port = int.Parse(a[1]);
        Packets();

        byte[] alice = File.ReadAllBytes(Path.Combine(dir, "alice.p12")), ca = File.ReadAllBytes(Path.Combine(dir, "trust.p12"));
        Check("tries passwords in order, as with WinTAK's saved one",
            VoiceLogin.Create("T", host, port, alice, new[] { null, "wrong", "atakatak" }, ca, new[] { "atakatak" }) != null);
        string why = "";
        try { VoiceLogin.Create("T", host, port, File.ReadAllBytes(Path.Combine(dir, "other.p12")), "wrong", ca, "atakatak"); }
        catch (SetupException e) { why = e.Message; }
        Check("no password fits: says why", why.Contains("3 passwords") || why.Contains("passwords ("), why);

        var bad = new Run();
        bad.Engine.Connect(Login(host, port, dir, "alice.p12", "rogue-trust.p12"), "WINTEST");
        Check("voice server from another CA refused", bad.Await(s => s.Error != null && s.Error.StartsWith("Refused"), 8000), bad.State?.Error ?? "");
        bad.Engine.Shutdown();

        var rogue = new Run();
        rogue.Engine.Connect(Login(host, port, dir, "rogue.p12", "trust.p12"), "ROGUE");
        Check("client certificate from another CA refused", rogue.Await(s => s.Error != null && !s.Connected && !s.Connecting, 10000), rogue.State?.Error ?? "");
        rogue.Engine.Shutdown();

        var run = new Run();
        run.Engine.Connect(Login(host, port, dir, "alice.p12", "trust.p12"), "WINTEST");
        Check("signs in with the TAK certificate", run.Await(s => s.Connected && s.Rows.Count > 0, 15000), run.State?.Error ?? "");
        Check("channel list arrives, root shown as LOBBY", run.State.Rows.Any(r => r.Here && r.Name == "LOBBY"));

        run.Engine.Create("E2E Team");
        bool moved = run.Await(s => s.ChannelName == "E2E Team", 5000);
        Check("makes a channel and moves into it", moved, run.State.ChannelName ?? "");
        if (moved)
        {
            Check("rejoin remembers the channel", run.Prefs.Get("last", "") == "E2E Team");
            run.Engine.Join(0);
            Check("moves back to the lobby", run.Await(s => s.ChannelName == "LOBBY", 5000));
        }

        Check("hears the bot's tone", run.Await(s => run.Audio.Loudest().peak > 3000, 12000), run.Audio.Loudest().ToString());
        Check("the tone is the bot's 660 Hz", Math.Abs(run.Audio.Loudest().hz - 660) < 40, run.Audio.Loudest().hz.ToString("F0") + " Hz");
        Check("shows who is talking", run.Await(s => s.Receiving != null, 8000), run.State.Receiving ?? "");

        run.Engine.PttDown();
        Check("hold to talk transmits", run.Await(s => s.Talking, 2000));
        Thread.Sleep(4000);
        run.Engine.PttUp();
        Check("release stops", run.Await(s => !s.Talking, 2000));
        Check("microphone ran for the press", run.Audio.MicFrames > 150, run.Audio.MicFrames + " frames");

        run.Engine.SetMode(VoiceEngine.ModeLatch);
        Thread.Sleep(150);
        run.Engine.PttDown();
        run.Await(s => s.Talking, 1000);
        run.Engine.PttUp();
        Thread.Sleep(300);
        Check("tap mode keeps talking after release", run.State.Talking);
        run.Engine.PttDown();
        Check("second tap stops", run.Await(s => !s.Talking, 2000));
        run.Engine.SetMode(VoiceEngine.ModeHold);

        run.Engine.SetTimeout(20);                              // the shortest limit
        Thread.Sleep(150);
        run.Engine.PttDown();
        bool cut = run.Await(s => s.Note != null && s.Note.StartsWith("Talk time limit"), 23000);
        Check("time limit cuts off talking", cut && !run.State.Talking, run.State.Note ?? "");

        var second = new Run();
        second.Engine.Connect(Login(host, port, dir, "alice.p12", "trust.p12"), "WINTEST2");
        bool took = second.Await(s => s.Connected, 15000);
        bool kicked = run.Await(s => s.Error != null && s.Error.Contains("another device"), 8000);
        Check("same user elsewhere: server allows one session", took);
        Check("the first device stops instead of fighting for it", kicked, run.State.Error ?? "");
        Thread.Sleep(7000);
        Check("second session stays up, no reconnect storm", second.State.Connected);

        Console.WriteLine(_failures == 0 ? "ALL PASSED" : _failures + " FAILED");
        return _failures == 0 ? 0 : 1;
    }
}
