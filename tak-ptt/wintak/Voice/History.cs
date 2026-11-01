using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Threading.Tasks;
using Concentus.Structs;

namespace TakPtt.Voice
{
#pragma warning disable CS0649          // the JSON reader fills these in
    /// <summary>One saved transmission.</summary>
    [DataContract]
    internal sealed class Clip
    {
        [DataMember(Name = "id")] public long Id;
        [DataMember(Name = "channel")] public string Channel;
        [DataMember(Name = "sender")] public string Sender;
        [DataMember(Name = "start")] public double Start;
        [DataMember(Name = "duration")] public double Duration;
        [DataMember(Name = "lat")] public double? Lat;
        [DataMember(Name = "lon")] public double? Lon;
    }

    [DataContract]
    internal sealed class ClipList
    {
        [DataMember(Name = "clips")] public List<Clip> Clips;
    }
#pragma warning restore CS0649

    /// <summary>
    /// The PortableTAK voice history (the recorder on the voice server), at
    /// https://TAK server:8443/voice/. Signs in with the TAK certificate and trusts only the TAK
    /// server's certificate authority. Clips are kept on this computer to play again offline.
    /// </summary>
    internal sealed class HistoryClient
    {
        private const int TimeoutMs = 15000;
        private readonly string _dir;

        /// <summary>Testing only: another port than the TAK server's 8443.</summary>
        public int Port = 8443;

        public string TextFile { get { return Path.Combine(_dir, "transcripts.txt"); } }

        public HistoryClient(string cacheDir)
        {
            _dir = cacheDir;
            Directory.CreateDirectory(_dir);
        }

        private byte[] Get(VoiceLogin login, string path)
        {
            var request = (HttpWebRequest)WebRequest.Create("https://" + login.Host + ":" + Port + "/voice/" + path);
            request.Timeout = request.ReadWriteTimeout = TimeoutMs;
            request.ClientCertificates.Add(login.Client);
            // Pinned to the TAK server's authority; its certificate may not carry the address used here.
            request.ServerCertificateValidationCallback = (s, cert, chain, errors) => login.TrustServer(cert);
            try
            {
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var body = response.GetResponseStream())
                using (var all = new MemoryStream())
                {
                    body.CopyTo(all);
                    return all.ToArray();
                }
            }
            catch (WebException e) when (e.Response is HttpWebResponse)
            {
                int code = (int)((HttpWebResponse)e.Response).StatusCode;
                throw new IOException(code == 404 ? "this server has no voice history yet"
                    : code == 403 ? "the server refused this certificate" : "voice history: HTTP " + code);
            }
        }

        /// <summary>Recent clips, newest first. Also saved, so the list shows without a connection.</summary>
        public List<Clip> Fetch(VoiceLogin login)
        {
            var json = Get(login, "clips");
            var clips = Parse(json);
            File.WriteAllBytes(Path.Combine(_dir, "list.json"), json);
            return clips;
        }

        /// <summary>The list saved by the last fetch (for offline use).</summary>
        public List<Clip> Cached()
        {
            try
            {
                return Parse(File.ReadAllBytes(Path.Combine(_dir, "list.json")));
            }
            catch (Exception)
            {
                return new List<Clip>();
            }
        }

        private static List<Clip> Parse(byte[] json)
        {
            using (var s = new MemoryStream(json))
                return ((ClipList)new DataContractJsonSerializer(typeof(ClipList)).ReadObject(s)).Clips ?? new List<Clip>();
        }

        /// <summary>The clip's audio file, downloaded once (login may be null when offline).</summary>
        public byte[] Audio(VoiceLogin login, Clip clip)
        {
            string file = Path.Combine(_dir, clip.Id + ".opus");
            if (File.Exists(file) && new FileInfo(file).Length > 0) return File.ReadAllBytes(file);
            if (login == null) throw new IOException("not downloaded yet, and no connection");
            var audio = Get(login, "clips/" + clip.Id + ".opus");
            File.WriteAllBytes(file, audio);
            return audio;
        }

        /// <summary>Drop saved clips the server no longer lists (past its retention).</summary>
        public void Trim(List<Clip> current)
        {
            var keep = new HashSet<string>(current.Select(c => c.Id + ".opus"));
            foreach (var f in Directory.GetFiles(_dir, "*.opus"))
                if (!keep.Contains(Path.GetFileName(f)))
                    File.Delete(f);
        }
    }

    /// <summary>Reads the recorder's Ogg Opus files.</summary>
    internal static class OggOpus
    {
        private const int MaxFrame = 5760;      // 120 ms, the longest Opus packet

        public static short[] Decode(byte[] ogg)
        {
            var packets = Packets(ogg);
            if (packets.Count < 2 || packets[0].Length < 19 || !Encoding(packets[0]))
                throw new InvalidDataException("not an Opus file");
            int preSkip = packets[0][10] | (packets[0][11] << 8);
            var decoder = new OpusDecoder(Mixer.Rate, 1);
            var pcm = new List<short>();
            var frame = new short[MaxFrame];
            foreach (var p in packets.Skip(2))                  // after OpusHead and OpusTags
            {
                int n = decoder.Decode(p, 0, p.Length, frame, 0, MaxFrame, false);
                for (int i = 0; i < n; i++) pcm.Add(frame[i]);
            }
            return pcm.Skip(preSkip).ToArray();
        }

        private static bool Encoding(byte[] head)
        {
            return System.Text.Encoding.ASCII.GetString(head, 0, 8) == "OpusHead";
        }

        /// <summary>The packets in a file: each page lists segment lengths, and one under 255 ends a packet.</summary>
        public static List<byte[]> Packets(byte[] b)
        {
            var result = new List<byte[]>();
            var packet = new MemoryStream();
            int at = 0;
            while (at + 27 <= b.Length)
            {
                if (b[at] != 'O' || b[at + 1] != 'g' || b[at + 2] != 'g' || b[at + 3] != 'S')
                    throw new InvalidDataException("not an Ogg file");
                int segments = b[at + 26], body = at + 27 + segments;
                if (body > b.Length) break;                     // cut short: keep what is there
                for (int i = 0; i < segments; i++)
                {
                    int len = b[at + 27 + i];
                    if (body + len > b.Length) return result;
                    packet.Write(b, body, len);
                    body += len;
                    if (len < 255)
                    {
                        result.Add(packet.ToArray());
                        packet.SetLength(0);
                    }
                }
                at = body;
            }
            return result;
        }
    }

    /// <summary>
    /// Plays clips through the mixer, so they mix with tones and live voice. Live traffic always
    /// wins: Live pauses playback, and it goes on after.
    /// </summary>
    internal sealed class ClipPlayer : IClipSource
    {
        private readonly object _lock = new object();
        private short[] _pcm;
        private int _at;
        private bool _playing, _live;

        /// <summary>A clip played to its end.</summary>
        public event Action Finished;

        public bool Active { get { lock (_lock) return _pcm != null && _playing && !_live; } }

        public bool Loaded { get { lock (_lock) return _pcm != null; } }

        public bool Playing { get { lock (_lock) return _pcm != null && _playing; } }

        public double Seconds { get { lock (_lock) return _at / (double)Mixer.Rate; } }

        public double Length { get { lock (_lock) return _pcm == null ? 0 : _pcm.Length / (double)Mixer.Rate; } }

        public bool Live
        {
            set { lock (_lock) _live = value; }
        }

        public void Load(short[] pcm)
        {
            lock (_lock)
            {
                _pcm = pcm;
                _at = 0;
                _playing = true;
            }
        }

        public void Stop()
        {
            lock (_lock)
            {
                _pcm = null;
                _playing = false;
            }
        }

        public void Toggle()
        {
            lock (_lock)
            {
                if (_pcm == null) return;
                if (_at >= _pcm.Length) _at = 0;
                _playing = !_playing;
            }
        }

        public void Seek(double seconds)
        {
            lock (_lock)
            {
                if (_pcm != null) _at = Math.Max(0, Math.Min(_pcm.Length, (int)(seconds * Mixer.Rate)));
            }
        }

        bool IClipSource.Next(short[] frame)
        {
            bool ended = false;
            lock (_lock)
            {
                if (_pcm == null || !_playing || _live) return false;
                int n = Math.Min(frame.Length, _pcm.Length - _at);
                Array.Copy(_pcm, _at, frame, 0, n);
                _at += n;
                if (_at >= _pcm.Length)
                {
                    _playing = false;
                    ended = true;
                }
            }
            if (ended) Task.Run(() => Finished?.Invoke());
            return true;
        }
    }

    /// <summary>Speech to text, on this computer.</summary>
    internal interface ITranscriber
    {
        /// <summary>The words in 48 kHz mono audio; "" when none were recognized.</summary>
        string Transcribe(short[] pcm);
    }

    /// <summary>
    /// The HISTORY tab's logic: the list, which clips are unheard (the dot), and the play queue.
    /// PLAY UNHEARD goes through them oldest first.
    /// </summary>
    internal sealed class VoiceHistory
    {
        private readonly HistoryClient _client;
        private readonly IPrefs _prefs;
        private readonly Mixer _mixer;
        private readonly object _lock = new object();
        private readonly HashSet<long> _heard = new HashSet<long>();
        private readonly Queue<Clip> _queue = new Queue<Clip>();
        private readonly Dictionary<long, string> _text = new Dictionary<long, string>();
        private readonly HashSet<long> _pending = new HashSet<long>();
        private readonly string _textFile;
        private List<Clip> _clips = new List<Clip>();
        private Task _textWork = Task.CompletedTask;

        /// <summary>Set by the plugin; null where speech recognition is not available.</summary>
        public ITranscriber Transcriber;

        public readonly ClipPlayer Player = new ClipPlayer();

        /// <summary>The list, the clip playing or the status line changed.</summary>
        public event Action Changed;

        public volatile string Status = "";

        /// <summary>Why speech to text is not working properly. Stays until it works: Status changes with every refresh.</summary>
        public volatile string TextProblem = "";

        public Clip Current { get; private set; }

        public Func<VoiceLogin> Login = () => null;

        /// <summary>Our callsign: our own transmissions never count as unheard.</summary>
        public string Me;

        public VoiceHistory(HistoryClient client, IPrefs prefs, Mixer mixer)
        {
            _client = client;
            _prefs = prefs;
            _mixer = mixer;
            mixer.Clip = Player;
            foreach (var id in prefs.Get("heard", "").Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
            {
                long n;
                if (long.TryParse(id, out n)) _heard.Add(n);
            }
            _clips = Shown(client.Cached());
            _textFile = client.TextFile;
            LoadText();
            Player.Finished += PlayNext;
        }

        /// <summary>Only clips after the last CLEAR on this computer (the server keeps them for everyone else).</summary>
        private List<Clip> Shown(List<Clip> clips)
        {
            double cleared;
            double.TryParse(_prefs.Get("cleared", "0"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out cleared);
            return clips.Where(c => c.Start > cleared).ToList();
        }

        /// <summary>Removes the listed transmissions and saved recordings from this computer only.</summary>
        public void Clear()
        {
            lock (_lock)
            {
                if (_clips.Count > 0)
                    _prefs.Put("cleared", _clips.Max(c => c.Start).ToString("R", System.Globalization.CultureInfo.InvariantCulture));
                _queue.Clear();
                _clips = new List<Clip>();
                _heard.Clear();
                SaveHeard();
                _text.Clear();
                SaveText();
            }
            Player.Stop();
            Current = null;
            _client.Trim(new List<Clip>());
            Say("");
        }

        /// <summary>Show what was said under each clip (setting).</summary>
        public bool TextOn
        {
            get { return _prefs.Get("text", "0") == "1"; }
            set
            {
                _prefs.Put("text", value ? "1" : "0");
                if (value) TranscribeAll();
                Changed?.Invoke();
            }
        }

        /// <summary>Show clips as markers on this computer's map (setting).</summary>
        public bool MapOn
        {
            get { return _prefs.Get("map", "0") == "1"; }
            set
            {
                _prefs.Put("map", value ? "1" : "0");
                Changed?.Invoke();
            }
        }

        /// <summary>What was said, or null if not worked out (yet).</summary>
        public string Text(Clip c)
        {
            string s;
            lock (_lock) return _text.TryGetValue(c.Id, out s) ? s : null;
        }

        private void LoadText()
        {
            try
            {
                if (!File.Exists(_textFile)) return;
                foreach (var line in File.ReadAllLines(_textFile))
                {
                    int tab = line.IndexOf('\t');
                    long id;
                    if (tab > 0 && long.TryParse(line.Substring(0, tab), out id)) _text[id] = line.Substring(tab + 1);
                }
            }
            catch (IOException)
            {
                // worked out again when needed
            }
        }

        private void SaveText()
        {
            try { File.WriteAllLines(_textFile, _text.Select(p => p.Key + "\t" + p.Value)); }
            catch (IOException) { }
        }

        /// <summary>One clip at a time in the background, newest first; each is only done once.</summary>
        public Task TranscribeAll()
        {
            var t = Transcriber;
            if (t == null || !TextOn) return Task.CompletedTask;
            List<Clip> todo;
            lock (_lock)
            {
                todo = _clips.Where(c => !_text.ContainsKey(c.Id) && _pending.Add(c.Id)).ToList();
                _textWork = _textWork.ContinueWith(_ =>
                {
                    foreach (var c in todo)
                    {
                        try
                        {
                            var words = t.Transcribe(OggOpus.Decode(_client.Audio(Login(), c)));
                            lock (_lock)
                            {
                                _text[c.Id] = words.Replace('\n', ' ').Replace('\t', ' ').Trim();
                                SaveText();
                            }
                            Changed?.Invoke();
                        }
                        catch (Exception e)
                        {
                            Say("Could not work out the text: " + e.GetBaseException().Message);
                        }
                        lock (_lock) _pending.Remove(c.Id);
                    }
                });
                return _textWork;
            }
        }

        public List<Clip> Clips { get { lock (_lock) return _clips.ToList(); } }

        public bool Unheard(Clip c) { lock (_lock) return !_heard.Contains(c.Id); }

        public int UnheardCount { get { lock (_lock) return _clips.Count(c => !_heard.Contains(c.Id)); } }

        /// <summary>Live traffic pauses playback, and it goes on after.</summary>
        public bool Live
        {
            set
            {
                Player.Live = value;
                if (!value) _mixer.Kick();
            }
        }

        /// <summary>Fetch the list on a background thread.</summary>
        public Task Refresh()
        {
            return Task.Run(() =>
            {
                var login = Login();
                if (login == null)
                {
                    Say("Offline: showing saved clips");
                    return;
                }
                try
                {
                    var clips = Shown(_client.Fetch(login));
                    _client.Trim(clips);
                    lock (_lock)
                    {
                        _clips = clips;
                        _heard.IntersectWith(clips.Select(c => c.Id));
                        _heard.UnionWith(clips.Where(c => c.Sender == Me).Select(c => c.Id));   // nobody needs to hear themselves
                        SaveHeard();
                    }
                    TranscribeAll();
                    Say(clips.Count == 0 ? "No transmissions saved yet" : "");
                }
                catch (Exception e)
                {
                    Say("Could not update: " + e.GetBaseException().Message);
                }
            });
        }

        public Task Play(Clip clip)
        {
            lock (_lock) _queue.Clear();
            return Start(clip);
        }

        /// <summary>Play the unheard clips in order, oldest first.</summary>
        public Task PlayUnheard()
        {
            Clip first;
            lock (_lock)
            {
                _queue.Clear();
                foreach (var c in _clips.Where(c => !_heard.Contains(c.Id)).OrderBy(c => c.Start)) _queue.Enqueue(c);
                first = _queue.Count > 0 ? _queue.Dequeue() : null;
            }
            return first == null ? Task.CompletedTask : Start(first);
        }

        /// <summary>Start the sound output after a change to the player.</summary>
        public void Kick() { _mixer.Kick(); }

        public void Toggle()
        {
            Player.Toggle();
            _mixer.Kick();
            Changed?.Invoke();
        }

        public void Stop()
        {
            lock (_lock) _queue.Clear();
            Player.Stop();
            Current = null;
            Changed?.Invoke();
        }

        private void PlayNext()
        {
            Clip next;
            lock (_lock) next = _queue.Count > 0 ? _queue.Dequeue() : null;
            if (next != null) Start(next);
            else Changed?.Invoke();
        }

        private Task Start(Clip clip)
        {
            Say("Loading " + clip.Sender);
            return Task.Run(() =>
            {
                try
                {
                    var pcm = OggOpus.Decode(_client.Audio(Login(), clip));
                    Current = clip;
                    Player.Load(pcm);
                    _mixer.Kick();
                    lock (_lock)
                    {
                        _heard.Add(clip.Id);
                        SaveHeard();
                    }
                    Say("");
                }
                catch (Exception e)
                {
                    Say("Could not play: " + e.GetBaseException().Message);
                }
            });
        }

        private void SaveHeard() { _prefs.Put("heard", string.Join(",", _heard)); }

        private void Say(string status)
        {
            Status = status;
            Changed?.Invoke();
        }
    }
}
