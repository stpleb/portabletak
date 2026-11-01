using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using Concentus.Enums;
using Concentus.Structs;

namespace TakPtt.Voice
{
    /// <summary>Saved settings. WinTAK's plugin keeps them in a file; the test keeps them in memory.</summary>
    internal interface IPrefs
    {
        string Get(string key, string otherwise);

        void Put(string key, string value);
    }

    /// <summary>One channel row for the screen.</summary>
    internal sealed class Row
    {
        public int Id, People;
        public string Name;
        public bool Here, Active, Rx, Tx;

        public string Key { get { return Id + "|" + Name + "|" + People + "|" + Here + "|" + Active + "|" + Rx + "|" + Tx; } }
    }

    /// <summary>What the screen shows.</summary>
    internal sealed class VoiceState
    {
        public bool Connected, Connecting, Talking;
        public string ServerName, ChannelName, Receiving, Error, Note, Mode;
        public int Timeout;
        public bool TonesOn, Rejoin;
        public List<Row> Rows = new List<Row>();
    }

    /// <summary>
    /// The voice engine, with the same behaviour as the ATAK plugin's VoiceService: hold or tap to
    /// talk, a talk-permit tone before the microphone opens, a time limit with a warning chirp 5
    /// seconds before, RX and TX picks kept by channel name, full duplex.
    ///
    /// All state lives on one thread; public methods post to it.
    /// </summary>
    internal sealed class VoiceEngine : IMumbleListener
    {
        public const string ModeHold = "hold", ModeLatch = "latch", Lobby = "LOBBY";
        private const int RecorderId = 99999;           // the history recorder (99901-99905: the OpenMANET bridge)
        private const string RecorderName = "PortableTAK Recorder";    // reserved by the sign-in check
        private const int Target = 5;                   // our voice target for talking on several channels
        private const int Bitrate = 40000;              // as the phones
        private const int TalkingMs = 400;              // a user counts as talking this long after a packet
        private const int RetryMs = 5000, RetryStartingMs = 10000;

        private readonly IAudio _audio;
        private readonly IPrefs _prefs;
        private readonly Action<VoiceState> _listener;
        private readonly Mixer _mixer;
        private readonly BlockingCollection<Action> _main = new BlockingCollection<Action>();

        private VoiceLogin _login;
        private string _username, _error, _note, _pendingChannel;
        private MumbleClient _client;
        private bool _connected, _connecting, _keyed, _talking, _pendingTemporary;
        private readonly HashSet<int> _listened = new HashSet<int>();
        private readonly Dictionary<int, int> _heardAt = new Dictionary<int, int>();    // session to its last voice packet
        private Timer _openMic, _warn, _cutoff, _retry, _activity;
        private long _sequence;                         // counts on across presses, as real Mumble clients do
        private long _txStop;                           // moved on to end the microphone thread
        private int _voiceTarget;

        /// <summary>Where this computer is: {lat, lon}, or null if not known.</summary>
        public Func<double[]> Position;

        /// <summary>The sound output, shared with history playback.</summary>
        public Mixer Output { get { return _mixer; } }

        public VoiceEngine(IAudio audio, IPrefs prefs, Action<VoiceState> listener)
        {
            _audio = audio;
            _prefs = prefs;
            _listener = listener;
            _mixer = new Mixer(audio);
            new Thread(() =>
            {
                foreach (var a in _main.GetConsumingEnumerable())
                {
                    try { a(); }
                    catch (Exception e) { _error = e.Message; }
                }
            })
            { IsBackground = true, Name = "takptt-voice" }.Start();
        }

        private void Post(Action a)
        {
            if (!_main.IsAddingCompleted) _main.Add(a);
        }

        private Timer After(int ms, Action a) { return new Timer(_ => Post(a), null, ms, Timeout.Infinite); }

        // ------------------------------------------------------------------ what the screen calls

        public void Connect(VoiceLogin login, string callsign)
        {
            Post(() =>
            {
                if (login == _login && (_connected || _connecting)) return;
                _login = login;
                _username = string.IsNullOrEmpty(callsign) ? login.User : callsign;
                _error = null;
                Open();
            });
        }

        /// <summary>No TAK connection to use; shown until Connect is called.</summary>
        public void Problem(string message)
        {
            Post(() =>
            {
                _error = message;
                Push();
            });
        }

        public void Shutdown()
        {
            Post(() =>
            {
                _login = null;
                Cancel(ref _retry);
                Cancel(ref _activity);
                StopTalking();
                CloseClient();
                _mixer.Stop();
                _main.CompleteAdding();
            });
        }

        public void PttDown()
        {
            Post(() =>
            {
                if (_keyed && Mode == ModeLatch) { StopTalking(); return; }
                if (_keyed) return;
                if (_client == null || !_client.IsSynced)
                {
                    Tone(_connecting ? Tones.NoChannel : Tones.Deny);
                    _note = _connecting ? "Still connecting" : "Not connected to voice";
                    Push();
                    return;
                }
                if (TxIds().Count == 0)
                {
                    Tone(Tones.NoChannel);
                    _note = "No TX channel picked";
                    Push();
                    return;
                }
                ApplyTarget();
                _keyed = true;
                int delay = TonesOn ? _mixer.Play(Tones.Permit) + 60 : 0;   // open the mic after the tone has played
                _openMic = After(delay, StartTalking);
                Push();
            });
        }

        /// <summary>A release ends talking in hold mode (tap mode ignores releases).</summary>
        public void PttUp()
        {
            Post(() => { if (Mode != ModeLatch) StopTalking(); });
        }

        public void Join(int channelId)
        {
            Post(() =>
            {
                if (_client == null || !_client.IsSynced) return;
                SetPick("rx", channelId, false);                    // now in it: no need to listen
                Safely(() => _client.Join(channelId));
            });
        }

        public void Create(string name)
        {
            Post(() =>
            {
                if (_client == null || !_client.IsSynced) return;
                _pendingChannel = name;
                _pendingTemporary = false;
                Safely(() => _client.CreateChannel(0, name, false));
            });
        }

        public void SetRx(int channelId, bool on) { Post(() => { SetPick("rx", channelId, on); ApplyListening(); Push(); }); }

        public void SetTx(int channelId, bool on) { Post(() => { SetPick("tx", channelId, on); ApplyTarget(); Push(); }); }

        public void SetMode(string mode)
        {
            Post(() =>
            {
                _prefs.Put("mode", mode);
                if (mode == ModeHold && _keyed && !_talking) StopTalking();
                Push();
            });
        }

        public void SetTimeout(int seconds) { Post(() => { _prefs.Put("timeout", seconds.ToString()); Push(); }); }

        public void SetTones(bool on) { Post(() => { _prefs.Put("tones", on ? "1" : "0"); Push(); }); }

        public void SetRejoin(bool on) { Post(() => { _prefs.Put("rejoin", on ? "1" : "0"); Push(); }); }

        // ------------------------------------------------------------------ settings

        private string Mode { get { return _prefs.Get("mode", ModeHold); } }

        private int TimeoutSeconds
        {
            get
            {
                int s;
                return int.TryParse(_prefs.Get("timeout", "60"), out s) ? Math.Max(20, Math.Min(90, s)) : 60;
            }
        }

        private bool Flag(string key) { return _prefs.Get(key, "1") == "1"; }

        private bool TonesOn { get { return Flag("tones"); } }

        private void Tone(int[][] steps) { if (TonesOn) _mixer.Play(steps); }

        // ------------------------------------------------------------------ connecting

        private void Open()
        {
            Cancel(ref _retry);
            CloseClient();
            if (_login == null) return;
            _connecting = true;
            _connected = false;
            _client = new MumbleClient(_login, _username, this);
            _client.Start();
            Push();
        }

        private void CloseClient()
        {
            _client?.Close();
            _client = null;
            _listened.Clear();
            _heardAt.Clear();
        }

        private void Retry(int ms)
        {
            Cancel(ref _retry);
            _retry = After(ms, Open);
        }

        void IMumbleListener.OnSynced(MumbleClient from)
        {
            Post(() =>
            {
                if (from != _client) return;
                _connected = true;
                _connecting = false;
                _error = null;
                _listened.Clear();                                  // a new session: listen and target again
                _voiceTarget = 0;
                RejoinLast();
                ApplyListening();
                ApplyTarget();
                if (_activity == null)
                    _activity = new Timer(_ => Post(CheckActivity), null, 200, 200);
                Push();
            });
        }

        void IMumbleListener.OnChanged()
        {
            Post(() =>
            {
                if (_client == null || !_client.IsSynced) return;
                MadeChannel();
                RememberChannel();
                ApplyListening();
                ApplyTarget();
                Push();
            });
        }

        void IMumbleListener.OnClosed(MumbleClient from, Closed why)
        {
            Post(() =>
            {
                if (why.Reason == CloseReason.Asked || from != _client) return;     // we closed it; a new one may be running
                bool wasTalking = _talking || _keyed;
                _connected = _connecting = false;
                StopTalking();
                if (wasTalking) Tone(Tones.Interrupt);
                _client = null;
                _heardAt.Clear();
                switch (why.Reason)
                {
                    case CloseReason.Handshake:
                        // Only a voice server with a certificate from the TAK server's authority is trusted.
                        _error = "Refused: the voice server's certificate is not from your TAK server";
                        break;
                    case CloseReason.Kicked:
                        // Murmur lets one voice ID in once: retrying here would throw the other device off.
                        _error = "Voice is signed in on another device with this user"
                            + (why.Text.Length == 0 ? "" : " (" + why.Text + ")") + ". Reconnect to take it back.";
                        break;
                    case CloseReason.Rejected:
                        if (why.RejectType == MumbleClient.RejectWrongServerPw)
                        {
                            // PortableTAK lets nobody in while its sign-in check is starting or restarting.
                            _error = "Voice server starting, trying again";
                            Retry(RetryStartingMs);
                        }
                        else if (why.RejectType == MumbleClient.RejectWrongUserPw)
                            _error = "Voice sign-in refused. Ask your PortableTAK admin to check your account.";
                        else
                            _error = "Voice sign-in refused: " + why.Text;
                        break;
                    default:
                        _error = "Voice disconnected, trying again: " + why.Text;
                        if (_login != null) Retry(RetryMs);
                        break;
                }
                Push();
            });
        }

        // ------------------------------------------------------------------ push to talk

        private void StartTalking()
        {
            if (!_keyed || _client == null) return;
            _talking = true;
            long id = Interlocked.Increment(ref _txStop);
            var c = _client;
            int target = _voiceTarget;
            SendPosition();
            new Thread(() => Transmit(c, target, id)) { IsBackground = true, Name = "takptt-mic" }.Start();
            int limit = TimeoutSeconds;
            _warn = After((limit - 5) * 1000, () => { if (_talking) Tone(Tones.Warning); });
            _cutoff = After(limit * 1000, () =>
            {
                if (!_talking) return;
                StopTalking();
                Tone(Tones.Cutoff);
                _note = "Talk time limit (" + TimeoutSeconds + " s) reached";
                Push();
            });
            Push();
        }

        /// <summary>Tell the voice history recorder where we are as we key up (shown with the clip).</summary>
        private void SendPosition()
        {
            var at = Position?.Invoke();
            var recorder = _client.Users.FirstOrDefault(u => u.UserId == RecorderId || u.Name == RecorderName);
            if (at == null || recorder == null) return;
            string text = string.Format(System.Globalization.CultureInfo.InvariantCulture, "takptt-pos {0:F6},{1:F6}", at[0], at[1]);
            Safely(() => _client.TextTo(recorder.Session, text));
        }

        private void StopTalking()
        {
            _keyed = false;
            Cancel(ref _openMic);
            Cancel(ref _warn);
            Cancel(ref _cutoff);
            if (_talking) Interlocked.Increment(ref _txStop);
            _talking = false;
            Push();
        }

        /// <summary>Microphone to server, on its own thread until StopTalking moves _txStop on.</summary>
        private void Transmit(MumbleClient c, int target, long id)
        {
            try
            {
                using (var mic = _audio.OpenMic())
                {
                    var enc = new OpusEncoder(Mixer.Rate, 1, OpusApplication.OPUS_APPLICATION_VOIP) { Bitrate = Bitrate, SignalType = OpusSignal.OPUS_SIGNAL_VOICE };
                    var pcm = new short[Mixer.Frame];
                    var opus = new byte[1275];
                    while (Interlocked.Read(ref _txStop) == id)
                    {
                        if (!mic.Read(pcm)) throw new InvalidOperationException("The microphone stopped");
                        bool last = Interlocked.Read(ref _txStop) != id;
                        int n = enc.Encode(pcm, 0, Mixer.Frame, opus, 0, opus.Length);
                        c.Voice(VoicePacket.Encode(target, Interlocked.Add(ref _sequence, 2), opus, n, last));     // counted in 10 ms steps
                        if (last) return;
                    }
                    int end = enc.Encode(new short[Mixer.Frame], 0, Mixer.Frame, opus, 0, opus.Length);
                    c.Voice(VoicePacket.Encode(target, Interlocked.Add(ref _sequence, 2), opus, end, true));     // tells receivers we stopped
                }
            }
            catch (Exception e)
            {
                Post(() =>
                {
                    if (Interlocked.Read(ref _txStop) != id) return;
                    StopTalking();
                    Tone(Tones.Deny);
                    _note = "Microphone not available: " + e.Message;
                    Push();
                });
            }
        }

        // ------------------------------------------------------------------ receiving

        void IMumbleListener.OnVoice(VoicePacket p)
        {
            _mixer.Add(p.Session, p.Data, p.Last);
            Post(() =>
            {
                bool fresh = !_heardAt.ContainsKey(p.Session);
                _heardAt[p.Session] = Environment.TickCount;
                if (fresh) Push();
            });
        }

        private void CheckActivity()
        {
            int now = Environment.TickCount;
            var gone = _heardAt.Where(p => now - p.Value > TalkingMs).Select(p => p.Key).ToList();
            foreach (int s in gone) _heardAt.Remove(s);
            string mixError = _mixer.Error;
            bool changed = gone.Count > 0;
            if (mixError != null && _error != "Speaker not available: " + mixError)
            {
                _error = "Speaker not available: " + mixError;
                changed = true;
            }
            if (changed) Push();
        }

        void IMumbleListener.OnDenied(int type, string reason)
        {
            Post(() =>
            {
                if (_pendingChannel != null)
                {
                    if (!_pendingTemporary)
                    {
                        // This server lets users make only temporary channels (gone when empty).
                        _pendingTemporary = true;
                        _note = "Making " + _pendingChannel + " as a temporary channel";
                        Safely(() => _client.CreateChannel(0, _pendingChannel, true));
                    }
                    else
                    {
                        _pendingChannel = null;
                        _note = "This server does not let you make channels";
                    }
                }
                else
                    _note = "Not allowed: " + reason;
                Push();
            });
        }

        // ------------------------------------------------------------------ RX / TX channels

        // Besides the channel you are in, you can listen to other channels (RX) and talk to several
        // channels at once (TX). The picks are kept by channel name, so they come back after a
        // reconnect. Talking on the channel you are in is the "tx.here" pick (on by default).

        private static string Name(Channel c) { return c.Id == 0 ? Lobby : c.Name; }

        private void MadeChannel()
        {
            if (_pendingChannel == null) return;
            var made = _client.Channels.FirstOrDefault(c => c.Name == _pendingChannel);
            if (made == null) return;
            _note = "Made " + made.Name;
            _pendingChannel = null;
            Safely(() => _client.Join(made.Id));
        }

        private void RememberChannel()
        {
            var c = _client.GetChannel(_client.Here);
            if (c != null) _prefs.Put("last", Name(c));
        }

        /// <summary>Go back to the channel you were last in (setting, on by default).</summary>
        private void RejoinLast()
        {
            if (!Flag("rejoin")) return;
            string last = _prefs.Get("last", null);
            if (last == null) return;
            var c = _client.Tree().FirstOrDefault(x => Name(x) == last && x.Id != _client.Here);
            if (c != null) Safely(() => _client.Join(c.Id));
        }

        private HashSet<string> Picks(string kind)
        {
            string s = _prefs.Get(kind, "");
            return s.Length == 0 ? new HashSet<string>() : new HashSet<string>(s.Split('\n'));
        }

        private void SetPick(string kind, int channelId, bool on)
        {
            var c = _client?.GetChannel(channelId);
            if (c == null) return;
            if (c.Id == _client.Here && kind == "tx")
            {
                _prefs.Put("tx.here", on ? "1" : "0");
                return;
            }
            var s = Picks(kind);
            if (on) s.Add(Name(c)); else s.Remove(Name(c));
            _prefs.Put(kind, string.Join("\n", s));
        }

        private List<int> TxIds()
        {
            var ids = new List<int>();
            if (_client == null || !_client.IsSynced) return ids;
            int here = _client.Here;
            if (Flag("tx.here")) ids.Add(here);
            var tx = Picks("tx");
            ids.AddRange(_client.Tree().Where(c => c.Id != here && tx.Contains(Name(c))).Select(c => c.Id));
            return ids;
        }

        /// <summary>Tell the server which channels to listen to (Mumble channel listeners).</summary>
        private void ApplyListening()
        {
            if (_client == null || !_client.IsSynced) return;
            var rx = Picks("rx");
            int here = _client.Here;
            var want = new HashSet<int>(_client.Tree().Where(c => c.Id != here && rx.Contains(Name(c))).Select(c => c.Id));
            var add = want.Where(id => !_listened.Contains(id)).ToList();
            var remove = _listened.Where(id => !want.Contains(id)).ToList();
            if (add.Count == 0 && remove.Count == 0) return;
            Safely(() => _client.Listen(add, remove));
            _listened.Clear();
            _listened.UnionWith(want);
        }

        /// <summary>Talk on the current channel only (normal), or on several channels through a voice target.</summary>
        private void ApplyTarget()
        {
            if (_client == null || !_client.IsSynced) return;
            var ids = TxIds();
            if (ids.Count == 1 && ids[0] == _client.Here)
            {
                _voiceTarget = 0;
                return;
            }
            _voiceTarget = Target;
            Safely(() => _client.VoiceTarget(Target, ids));
        }

        /// <summary>A failed send means the connection is going; OnClosed handles that.</summary>
        private static void Safely(Action a)
        {
            try { a(); }
            catch (Exception) { }
        }

        // ------------------------------------------------------------------ state for the screen

        private void Push()
        {
            var s = new VoiceState
            {
                Connected = _connected,
                Connecting = _connecting,
                Talking = _talking || _keyed,
                ServerName = _login?.ServerName,
                Error = _error,
                Note = _note,
                Mode = Mode,
                Timeout = TimeoutSeconds,
                TonesOn = TonesOn,
                Rejoin = Flag("rejoin")
            };
            _note = null;
            if (_client != null && _client.IsSynced)
            {
                int me = _client.Session, here = _client.Here;
                var people = new Dictionary<int, int>();
                var active = new HashSet<int>();
                foreach (var u in _client.Users)
                {
                    if (u.UserId == RecorderId || (u.UserId > 99900 && u.UserId <= 99905)) continue;   // not people
                    int count;
                    people.TryGetValue(u.ChannelId, out count);
                    people[u.ChannelId] = count + 1;
                    if (u.Session == me || !_heardAt.ContainsKey(u.Session)) continue;
                    active.Add(u.ChannelId);
                    var c = _client.GetChannel(u.ChannelId);
                    s.Receiving = u.ChannelId == here || c == null ? u.Name : u.Name + " · " + Name(c);
                }
                var cur = _client.GetChannel(here);
                s.ChannelName = cur == null ? "" : Name(cur);
                var rx = Picks("rx");
                var tx = TxIds();
                foreach (var c in _client.Tree())
                {
                    int count;
                    people.TryGetValue(c.Id, out count);
                    s.Rows.Add(new Row
                    {
                        Id = c.Id, Name = Name(c), People = count, Here = c.Id == here, Active = active.Contains(c.Id),
                        Rx = c.Id == here || rx.Contains(Name(c)), Tx = tx.Contains(c.Id)
                    });
                }
            }
            _listener(s);
        }

        private static void Cancel(ref Timer t)
        {
            t?.Dispose();
            t = null;
        }
    }
}
