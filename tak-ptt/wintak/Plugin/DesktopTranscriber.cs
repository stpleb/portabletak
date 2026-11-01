using System;
using System.IO;
using System.Speech.AudioFormat;
using System.Speech.Recognition;
using System.Text;
using System.Threading;
using TakPtt.Voice;

namespace TakPtt.WinTak
{
    /// <summary>
    /// Speech to text with Windows' own recognizer: offline, nothing leaves this computer. It is fed the
    /// clip's audio instead of the microphone.
    /// </summary>
    internal sealed class DesktopTranscriber : ITranscriber
    {
        private const int Rate = 16000, Step = Mixer.Rate / Rate, TimeoutMs = 120000;

        /// <summary>Null when Windows has no recognizer installed.</summary>
        public static ITranscriber Create()
        {
            try
            {
                return SpeechRecognitionEngine.InstalledRecognizers().Count > 0 ? new DesktopTranscriber() : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        public string Transcribe(short[] pcm)
        {
            var bytes = new byte[pcm.Length / Step * 2];
            for (int i = 0, o = 0; o < bytes.Length; i += Step, o += 2)
            {
                int sum = 0;
                for (int k = 0; k < Step; k++) sum += pcm[i + k];       // 48 kHz to 16 kHz
                short s = (short)(sum / Step);
                bytes[o] = (byte)s;
                bytes[o + 1] = (byte)(s >> 8);
            }
            var words = new StringBuilder();
            Exception failed = null;
            using (var engine = new SpeechRecognitionEngine())
            using (var audio = new MemoryStream(bytes))
            using (var done = new ManualResetEventSlim())
            {
                engine.LoadGrammar(new DictationGrammar());
                engine.SetInputToAudioStream(audio, new SpeechAudioFormatInfo(Rate, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
                engine.SpeechRecognized += (s, e) => words.Append(e.Result.Text).Append(' ');
                engine.RecognizeCompleted += (s, e) =>
                {
                    failed = e.Error;
                    done.Set();
                };
                // Calling Recognize again at the end of a stream throws, so one run takes the whole clip.
                engine.RecognizeAsync(RecognizeMode.Multiple);
                if (!done.Wait(TimeoutMs))
                {
                    engine.RecognizeAsyncCancel();
                    done.Wait(TimeoutMs);
                    throw new IOException("Windows speech took too long");
                }
            }
            if (failed != null) throw failed;
            return words.ToString().Trim();
        }
    }
}
