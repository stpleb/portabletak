using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace TakPtt.Voice
{
    /// <summary>
    /// Speech to text with Whisper, offline, in its own process (see TakPtt.Whisper): one run per clip.
    /// If it fails, the next recognizer takes over, so a computer Whisper cannot run on still gets text.
    /// </summary>
    internal sealed class WhisperTranscriber : ITranscriber
    {
        private const int Rate = 16000, Step = Mixer.Rate / Rate, TimeoutMs = 180000;
        private readonly string _program, _prefix, _model;
        private readonly ITranscriber _fallback;

        /// <summary>Why Whisper is not being used, or "" when it worked.</summary>
        public Action<string> Problem;

        /// <param name="program">The helper; prefix goes before its arguments (the test runs it through dotnet).</param>
        public WhisperTranscriber(string program, string prefix, string model, ITranscriber fallback)
        {
            _program = program;
            _prefix = prefix;
            _model = model;
            _fallback = fallback;
        }

        public string Transcribe(short[] pcm)
        {
            string file = Path.GetTempFileName();
            try
            {
                File.WriteAllBytes(file, Resample(pcm));
                string words = Clean(Run(file));
                Problem?.Invoke("");
                return words;
            }
            catch (Exception e)
            {
                Problem?.Invoke("Whisper did not run: " + e.Message);
                if (_fallback == null) throw;
                return _fallback.Transcribe(pcm);
            }
            finally
            {
                File.Delete(file);
            }
        }

        private string Run(string audio)
        {
            if (!File.Exists(_model)) throw new FileNotFoundException(_model + " is missing");
            var start = new ProcessStartInfo(_program, _prefix + "\"" + _model + "\" \"" + audio + "\"")
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8
            };
            using (var p = Process.Start(start))
            {
                var text = p.StandardOutput.ReadToEndAsync();
                var error = p.StandardError.ReadToEndAsync();
                if (!p.WaitForExit(TimeoutMs))
                {
                    p.Kill();
                    throw new IOException("Whisper took too long");
                }
                if (p.ExitCode != 0) throw new IOException("exit code 0x" + p.ExitCode.ToString("X") + " " + error.Result.Trim());
                return text.Result;
            }
        }

        /// <summary>48 kHz to raw 16 kHz, which is what Whisper takes.</summary>
        private static byte[] Resample(short[] pcm)
        {
            var bytes = new byte[pcm.Length / Step * 2];
            for (int i = 0, o = 0; o < bytes.Length; i += Step, o += 2)
            {
                int sum = 0;
                for (int k = 0; k < Step; k++) sum += pcm[i + k];
                short s = (short)(sum / Step);
                bytes[o] = (byte)s;
                bytes[o + 1] = (byte)(s >> 8);
            }
            return bytes;
        }

        /// <summary>Whisper writes sounds in brackets, such as [BLANK_AUDIO] or (static): those are not words.</summary>
        internal static string Clean(string text)
        {
            return Regex.Replace(Regex.Replace(text, @"[\[(][^\])]*[\])]", " "), @"\s+", " ").Trim();
        }
    }
}
