using System;
using System.IO;
using System.Linq;
using System.Threading;
using TakPtt.Voice;

// The HISTORY tab's code against the real recorder: talk, then find the clip, download it, decode it and
// play it, with the same made-up speaker as the voice test. See tools/run-history-e2e.sh.
internal static class HistoryTest
{
    private static VoiceLogin Login(string host, int port, string dir, string client)
    {
        return VoiceLogin.Create("Test TAK", host, port, File.ReadAllBytes(Path.Combine(dir, client)), "atakatak",
            File.ReadAllBytes(Path.Combine(dir, "trust.p12")), "atakatak");
    }

    private sealed class FakeText : ITranscriber
    {
        public int Calls;

        public string Transcribe(short[] pcm)
        {
            Interlocked.Increment(ref Calls);
            return "heard " + pcm.Length / 48000 + " s";
        }
    }

    private static int Frames(Program.FakeAudio audio)
    {
        lock (audio.Heard) return audio.Heard.Count(f => f.Max(s => Math.Abs((int)s)) > 2000);
    }

    public static int Run(string host, int voicePort, int historyPort, string dir)
    {
        var run = new Program.Run();
        var login = Login(host, voicePort, dir, "alice.p12");
        run.Engine.Position = () => new[] { 32.7767, -96.797 };
        run.Engine.Connect(login, "HISTTEST");
        Program.Check("signs in", run.Await(s => s.Connected, 15000), run.State?.Error ?? "");
        for (int i = 0; i < 2; i++)
        {
            run.Engine.PttDown();
            run.Await(s => s.Talking, 2000);
            Thread.Sleep(3000);
            run.Engine.PttUp();
            run.Await(s => !s.Talking, 2000);
            Thread.Sleep(2500);                         // the recorder closes a clip after a quiet moment
        }

        string cache = Path.Combine(Path.GetTempPath(), "takptt-history-" + Guid.NewGuid());
        var client = new HistoryClient(cache) { Port = historyPort };
        var history = new VoiceHistory(client, run.Prefs, run.Engine.Output) { Login = () => login };
        for (int i = 0; i < 20 && history.Clips.Count(c => c.Sender == "HISTTEST") < 2; i++)
        {
            history.Refresh().Wait();
            Thread.Sleep(1000);
        }
        var clips = history.Clips.Where(c => c.Sender == "HISTTEST").ToList();
        Program.Check("lists the transmissions, with who, channel and length", clips.Count == 2 && clips[0].Channel == "LOBBY" && clips[0].Duration > 2,
            history.Status + " " + string.Join(", ", history.Clips.Select(c => c.Sender + "/" + c.Channel + "/" + c.Duration.ToString("F1"))));
        if (clips.Count < 2) return 1;
        Program.Check("the clip has the position sent at key-up", clips[0].Lat != null && Math.Abs(clips[0].Lat.Value - 32.7767) < 0.0001 && Math.Abs(clips[0].Lon.Value + 96.797) < 0.0001, clips[0].Lat + "," + clips[0].Lon);
        Program.Check("clips start unheard", history.UnheardCount == 2, history.UnheardCount.ToString());

        var newest = clips[0];
        run.Audio.Heard.Clear();
        history.Play(newest).Wait();
        Program.Check("downloads and decodes the clip", history.Player.Loaded && Math.Abs(history.Player.Length - newest.Duration) < 0.5,
            history.Status + " " + history.Player.Length.ToString("F2") + " s, listed " + newest.Duration.ToString("F2"));
        Thread.Sleep(1000);
        Program.Check("plays it through the speaker", Frames(run.Audio) > 30, Frames(run.Audio) + " loud frames");
        var (peak, hz) = run.Audio.Loudest();
        Program.Check("it is what was said (440 Hz)", Math.Abs(hz - 440) < 30, peak + " peak, " + hz.ToString("F0") + " Hz");

        history.Live = true;
        Thread.Sleep(150);
        double at = history.Player.Seconds;
        Thread.Sleep(600);
        Program.Check("live traffic pauses playback", Math.Abs(history.Player.Seconds - at) < 0.05, at.ToString("F2"));
        history.Live = false;
        Thread.Sleep(400);
        Program.Check("and it goes on after", history.Player.Seconds > at + 0.1, history.Player.Seconds.ToString("F2"));

        history.Toggle();
        Thread.Sleep(100);
        at = history.Player.Seconds;
        Thread.Sleep(400);
        Program.Check("pause", !history.Player.Playing && Math.Abs(history.Player.Seconds - at) < 0.05);
        history.Player.Seek(0.5);
        Program.Check("scrub", Math.Abs(history.Player.Seconds - 0.5) < 0.01, history.Player.Seconds.ToString("F2"));
        history.Stop();
        Program.Check("played clip no longer unheard", history.UnheardCount == 1, history.UnheardCount.ToString());

        string finished = "";
        history.Changed += () => { if (history.Current != null && !history.Player.Playing) finished = "done"; };
        history.PlayUnheard().Wait();
        Program.Check("PLAY UNHEARD starts with the oldest", history.Current != null && history.Current.Id == clips[1].Id, history.Current?.Id.ToString() ?? "");
        var deadline = DateTime.UtcNow.AddSeconds(8);
        while (history.UnheardCount > 0 && DateTime.UtcNow < deadline) Thread.Sleep(100);
        Program.Check("all heard", history.UnheardCount == 0);
        while (history.Player.Playing && DateTime.UtcNow < deadline) Thread.Sleep(100);
        Thread.Sleep(200);
        Program.Check("plays to the end and stops", !history.Player.Playing && finished == "done");

        var offline = new VoiceHistory(new HistoryClient(cache) { Port = historyPort }, new Program.MemoryPrefs(), run.Engine.Output);
        Program.Check("list is saved for offline", offline.Clips.Count(c => c.Sender == "HISTTEST") == 2);
        offline.Play(newest).Wait();
        Program.Check("saved clip plays offline", offline.Player.Loaded, offline.Status);
        offline.Stop();

        var said = new FakeText();
        history.Transcriber = said;
        history.TextOn = true;
        history.TranscribeAll().Wait();
        Program.Check("works out each clip once", said.Calls == 2 && history.Text(newest) == "heard 3 s", said.Calls + " calls: " + history.Text(newest));
        var reopened = new VoiceHistory(new HistoryClient(cache) { Port = historyPort }, new Program.MemoryPrefs(), run.Engine.Output);
        Program.Check("text is kept", reopened.Text(newest) == "heard 3 s");

        history.Clear();
        Program.Check("CLEAR empties the list and saved clips here", history.Clips.Count == 0 && Directory.GetFiles(cache, "*.opus").Length == 0 && history.Text(newest) == null);
        history.Refresh().Wait();
        Program.Check("cleared clips stay gone after a refresh", history.Clips.Count == 0, history.Status);
        history.Me = "HISTTEST";
        run.Engine.PttDown();
        run.Await(s => s.Talking, 2000);
        Thread.Sleep(2000);
        run.Engine.PttUp();
        for (int i = 0; i < 15 && history.Clips.Count == 0; i++)
        {
            Thread.Sleep(1000);
            history.Refresh().Wait();
        }
        Program.Check("a new transmission shows after a clear", history.Clips.Count == 1, history.Clips.Count.ToString());

        Program.Check("your own transmissions don't count as unheard", history.UnheardCount == 0, history.UnheardCount.ToString());

        var rogue = new HistoryClient(cache) { Port = historyPort };
        string refused = "";
        try { rogue.Fetch(Login(host, voicePort, dir, "rogue.p12")); }
        catch (Exception e) { refused = e.Message; }
        Program.Check("certificate from another CA gets no clips", refused.Length > 0, refused);

        Directory.Delete(cache, true);
        Console.WriteLine(Program._failures == 0 ? "ALL PASSED" : Program._failures + " FAILED");
        return Program._failures == 0 ? 0 : 1;
    }
}
