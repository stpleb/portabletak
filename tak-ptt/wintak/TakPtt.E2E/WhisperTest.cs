using System;
using System.IO;
using System.Linq;
using Concentus.Enums;
using Concentus.Structs;
using TakPtt.Voice;

// Real speech through the plugin's path: 48 kHz audio, Opus at the plugin's settings and back, then the helper.
//   whisper HELPER_DLL MODEL SPEECH_48K_RAW EXPECTED|WORDS
internal static class WhisperTest
{
    public static int Run(string helper, string model, string speech, string expected)
    {
        var bytes = File.ReadAllBytes(speech);
        var pcm = new short[bytes.Length / 2];
        Buffer.BlockCopy(bytes, 0, pcm, 0, bytes.Length);
        var enc = new OpusEncoder(48000, 1, OpusApplication.OPUS_APPLICATION_VOIP) { Bitrate = 40000, SignalType = OpusSignal.OPUS_SIGNAL_VOICE };
        var dec = new OpusDecoder(48000, 1);
        var radio = new short[pcm.Length / 960 * 960];
        var packet = new byte[1275];
        var frame = new short[960];
        for (int at = 0; at + 960 <= radio.Length; at += 960)
        {
            int n = enc.Encode(pcm, at, 960, packet, 0, packet.Length);
            dec.Decode(packet, 0, n, frame, 0, 960, false);
            Array.Copy(frame, 0, radio, at, 960);
        }
        var started = DateTime.UtcNow;
        string text = new WhisperTranscriber("dotnet", "\"" + helper + "\" ", model, null).Transcribe(radio);
        double seconds = (DateTime.UtcNow - started).TotalSeconds;
        Console.WriteLine("HEARD: " + text);
        Console.WriteLine("took " + seconds.ToString("F1") + " s for " + (radio.Length / 48000.0).ToString("F1") + " s of audio");
        var missing = expected.Split(' ').Where(w => text.IndexOf(w, StringComparison.OrdinalIgnoreCase) < 0).ToList();
        Program.Check("the words came through", missing.Count == 0, missing.Count == 0 ? "" : "missing " + string.Join(" ", missing));
        Program.Check("sounds in brackets are dropped", WhisperTranscriber.Clean("[BLANK_AUDIO] copy that (static) over") == "copy that over");

        string problem = "";
        var fallback = new WhisperTranscriber("/no/such/program", "", model, new Stand()) { Problem = p => problem = p };
        Program.Check("if Whisper cannot run, the next recognizer takes over", fallback.Transcribe(pcm) == "from the fallback");
        Program.Check("and it says why", problem.StartsWith("Whisper did not run: "), problem);
        var noModel = new WhisperTranscriber("dotnet", "\"" + helper + "\" ", "/no/such/model", new Stand()) { Problem = p => problem = p };
        noModel.Transcribe(pcm);
        Program.Check("a missing model is named", problem.Contains("/no/such/model"), problem);
        var failing = new WhisperTranscriber("dotnet", "\"" + helper + "\" ", speech, new Stand()) { Problem = p => problem = p };
        failing.Transcribe(pcm);
        Program.Check("the helper's own error is shown", problem.Contains("exit code 0x1"), problem);
        Console.WriteLine("shown to the user: " + problem);
        var working = new WhisperTranscriber("dotnet", "\"" + helper + "\" ", model, null) { Problem = p => problem = p };
        working.Transcribe(radio);
        Program.Check("the problem goes away once Whisper works", problem == "", problem);
        return Program._failures == 0 ? 0 : 1;
    }

    private sealed class Stand : ITranscriber
    {
        public string Transcribe(short[] pcm) { return "from the fallback"; }
    }
}
