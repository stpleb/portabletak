using System;
using System.IO;
using System.Text;
using Whisper.net;

namespace TakPtt.Whisper
{
    /// <summary>
    /// Prints the words in a recording.
    ///   TakPtt.Whisper MODEL AUDIO      AUDIO is raw 16 kHz mono 16-bit samples
    /// </summary>
    internal static class Program
    {
        private static int Main(string[] args)
        {
            if (args.Length != 2)
            {
                Console.Error.WriteLine("usage: TakPtt.Whisper MODEL AUDIO");
                return 2;
            }
            try
            {
                var bytes = File.ReadAllBytes(args[1]);
                var samples = new float[bytes.Length / 2];
                for (int i = 0; i < samples.Length; i++)
                    samples[i] = (short)(bytes[i * 2] | (bytes[i * 2 + 1] << 8)) / 32768f;
                var text = new StringBuilder();
                using (var factory = WhisperFactory.FromPath(args[0]))
                using (var processor = factory.CreateBuilder().WithLanguage("en").WithThreads(Math.Max(1, Math.Min(4, Environment.ProcessorCount)))
                    .WithSegmentEventHandler(s => text.Append(s.Text)).Build())
                    processor.Process(samples);
                var output = Console.OpenStandardOutput();
                var utf8 = Encoding.UTF8.GetBytes(text.ToString().Trim());
                output.Write(utf8, 0, utf8.Length);
                return 0;
            }
            catch (Exception e)
            {
                Console.Error.WriteLine(e.GetBaseException().Message);
                return 1;
            }
        }
    }
}
