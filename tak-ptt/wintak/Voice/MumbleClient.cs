using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using System.Threading;

namespace TakPtt.Voice
{
    internal enum CloseReason { Handshake, Rejected, Kicked, Network, Asked }

    internal sealed class Closed
    {
        public CloseReason Reason;
        public int RejectType;
        public string Text = "";
    }

    internal interface IMumbleListener
    {
        /// <summary>Signed in: the channel and user lists are complete.</summary>
        void OnSynced(MumbleClient from);

        /// <summary>The channel list or who is where changed.</summary>
        void OnChanged();

        void OnVoice(VoicePacket packet);

        void OnDenied(int type, string reason);

        void OnClosed(MumbleClient from, Closed why);
    }

    internal sealed class Channel
    {
        public readonly int Id;
        public volatile int Parent = -1;
        public volatile string Name = "";

        public Channel(int id) { Id = id; }
    }

    internal sealed class User
    {
        public readonly int Session;
        public volatile int UserId = -1, ChannelId;
        public volatile string Name = "";

        public User(int session) { Session = session; }
    }

    /// <summary>
    /// A small Mumble client over TLS. Voice goes through the TLS connection (Mumble's UDP tunnel),
    /// the same way the phones run over Tailscale, so it needs one TCP port and no firewall changes.
    /// One object is one connection: after OnClosed, make a new one.
    /// </summary>
    internal sealed class MumbleClient
    {
        // Mumble message types and the Reject types the engine looks at
        private const int Version = 0, Tunnel = 1, Authenticate = 2, Ping = 3, Reject = 4, ServerSync = 5,
            ChannelRemove = 6, ChannelState = 7, UserRemove = 8, UserState = 9, Text = 11,
            PermissionDenied = 12, VoiceTargetMsg = 19;
        public const int RejectWrongUserPw = 3, RejectWrongServerPw = 4;
        private const uint ClientVersion = (1 << 16) | (4 << 8);        // 1.4.0: the legacy voice format
        private const int MaxMessage = 8 * 1024 * 1024;
        private const int PingMs = 15000, ReadTimeoutMs = 45000, ConnectTimeoutMs = 10000;

        private readonly VoiceLogin _login;
        private readonly string _username;
        private readonly IMumbleListener _listener;
        private readonly ConcurrentDictionary<int, Channel> _channels = new ConcurrentDictionary<int, Channel>();
        private readonly ConcurrentDictionary<int, User> _users = new ConcurrentDictionary<int, User>();
        private readonly object _sendLock = new object();

        private TcpClient _tcp;
        private volatile Stream _out;
        private volatile bool _closing, _synced;
        private volatile int _session = -1;
        private Closed _closed;
        private Timer _pings;

        public MumbleClient(VoiceLogin login, string username, IMumbleListener listener)
        {
            _login = login;
            _username = username;
            _listener = listener;
        }

        public void Start()
        {
            new Thread(Run) { IsBackground = true, Name = "takptt-mumble" }.Start();
        }

        public void Close()
        {
            _closing = true;
            try { _tcp?.Close(); } catch (Exception) { }
        }

        public bool IsSynced { get { return _synced; } }

        public int Session { get { return _session; } }

        public ICollection<Channel> Channels { get { return _channels.Values; } }

        public ICollection<User> Users { get { return _users.Values; } }

        public Channel GetChannel(int id)
        {
            Channel c;
            return _channels.TryGetValue(id, out c) ? c : null;
        }

        /// <summary>The channel we are in, or -1 before sign-in.</summary>
        public int Here
        {
            get
            {
                User me;
                return _users.TryGetValue(_session, out me) ? me.ChannelId : -1;
            }
        }

        // ------------------------------------------------------------------ sending

        public void Join(int channelId) { Send(UserState, new Pb().UInt(1, (ulong)_session).UInt(5, (ulong)channelId).Done()); }

        public void CreateChannel(int parent, string name, bool temporary)
        {
            Send(ChannelState, new Pb().UInt(2, (ulong)parent).Str(3, name).Bool(8, temporary).Done());
        }

        /// <summary>Mumble channel listeners: hear these channels without joining them.</summary>
        public void Listen(IEnumerable<int> add, IEnumerable<int> remove)
        {
            var m = new Pb().UInt(1, (ulong)_session);
            foreach (int id in add) m.UInt(21, (ulong)id);
            foreach (int id in remove) m.UInt(22, (ulong)id);
            Send(UserState, m.Done());
        }

        public void VoiceTarget(int id, IEnumerable<int> channelIds)
        {
            var m = new Pb().UInt(1, (ulong)id);
            foreach (int c in channelIds) m.Bytes(2, new Pb().UInt(2, (ulong)c).Done());
            Send(VoiceTargetMsg, m.Done());
        }

        public void TextTo(int session, string text) { Send(Text, new Pb().UInt(2, (ulong)session).Str(5, text).Done()); }

        public void Voice(byte[] packet) { Send(Tunnel, packet); }

        private void Send(int type, byte[] body)
        {
            var o = _out;
            if (o == null) throw new IOException("not connected");
            var head = new byte[6];
            head[0] = (byte)(type >> 8);
            head[1] = (byte)type;
            head[2] = (byte)(body.Length >> 24);
            head[3] = (byte)(body.Length >> 16);
            head[4] = (byte)(body.Length >> 8);
            head[5] = (byte)body.Length;
            lock (_sendLock)
            {
                o.Write(head, 0, 6);
                o.Write(body, 0, body.Length);
                o.Flush();
            }
        }

        // ------------------------------------------------------------------ the connection

        private void Run()
        {
            try
            {
                _tcp = new TcpClient { NoDelay = true, ReceiveTimeout = ReadTimeoutMs };
                var connect = _tcp.ConnectAsync(_login.Host, _login.Port);
                if (!connect.Wait(ConnectTimeoutMs)) throw new IOException("No answer from " + _login.Host);
                var tls = new SslStream(_tcp.GetStream(), false, (s, cert, chain, errors) => _login.TrustServer(cert),
                    (s, host, local, remote, issuers) => _login.Client);
                tls.AuthenticateAsClient(_login.Host, new X509CertificateCollection { _login.Client }, SslProtocols.Tls12, false);
                _out = tls;
                Send(Version, new Pb().UInt(1, ClientVersion).Str(2, "TAK PTT for WinTAK")
                    .Str(3, Environment.OSVersion.Platform.ToString()).Str(4, Environment.OSVersion.VersionString).Done());
                Send(Authenticate, new Pb().Str(1, _username).Bool(5, true).Done());
                _pings = new Timer(_ => SendPing(), null, 0, PingMs);
                var input = new BufferedStream(tls);
                var head = new byte[6];
                while (true)
                {
                    ReadFully(input, head, 6);
                    int type = (head[0] << 8) | head[1];
                    int len = (head[2] << 24) | (head[3] << 16) | (head[4] << 8) | head[5];
                    if (len < 0 || len > MaxMessage) throw new IOException("message too long");
                    var body = new byte[len];
                    ReadFully(input, body, len);
                    Handle(type, body);
                }
            }
            catch (AuthenticationException e)
            {
                Finish(new Closed { Reason = CloseReason.Handshake, Text = e.Message });
            }
            catch (Exception e)
            {
                var inner = e is AggregateException && e.InnerException != null ? e.InnerException : e;
                Finish(new Closed { Reason = CloseReason.Network, Text = inner.Message });
            }
            finally
            {
                _pings?.Dispose();
                _synced = false;
                try { _tcp?.Close(); } catch (Exception) { }
                _listener.OnClosed(this, _closing ? new Closed { Reason = CloseReason.Asked } : _closed);
            }
        }

        private static void ReadFully(Stream s, byte[] b, int n)
        {
            int at = 0;
            while (at < n)
            {
                int r = s.Read(b, at, n - at);
                if (r <= 0) throw new EndOfStreamException("The server closed the connection");
                at += r;
            }
        }

        /// <summary>The first reason wins: a Reject comes before the socket closes.</summary>
        private void Finish(Closed why)
        {
            if (_closed == null) _closed = why;
        }

        private void SendPing()
        {
            try
            {
                Send(Ping, new Pb().UInt(1, (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()).Done());
            }
            catch (Exception)
            {
                // the reader sees the failure too
            }
        }

        private void Handle(int type, byte[] body)
        {
            switch (type)
            {
                case Tunnel:
                    var p = VoicePacket.Decode(body);
                    if (p != null) _listener.OnVoice(p);
                    break;
                case Reject:
                {
                    var m = Pb.Parse(body);
                    Finish(new Closed { Reason = CloseReason.Rejected, RejectType = (int)m.Num(1, 0), Text = m.Text(2, "") });
                    throw new EndOfStreamException();
                }
                case ServerSync:
                    _session = (int)Pb.Parse(body).Num(1, -1);
                    _synced = true;
                    _listener.OnSynced(this);
                    break;
                case ChannelState:
                {
                    var m = Pb.Parse(body);
                    int id = (int)m.Num(1, -1);
                    if (id < 0) break;
                    var c = _channels.GetOrAdd(id, i => new Channel(i));
                    if (m.Has(2)) c.Parent = (int)m.Num(2, -1);
                    if (m.Has(3)) c.Name = m.Text(3, "");
                    if (_synced) _listener.OnChanged();
                    break;
                }
                case ChannelRemove:
                {
                    Channel gone;
                    _channels.TryRemove((int)Pb.Parse(body).Num(1, -1), out gone);
                    if (_synced) _listener.OnChanged();
                    break;
                }
                case UserState:
                {
                    var m = Pb.Parse(body);
                    if (!m.Has(1)) break;
                    var u = _users.GetOrAdd((int)m.Num(1, -1), i => new User(i));
                    if (m.Has(3)) u.Name = m.Text(3, "");
                    if (m.Has(4)) u.UserId = (int)m.Num(4, -1);
                    if (m.Has(5)) u.ChannelId = (int)m.Num(5, 0);
                    if (_synced) _listener.OnChanged();
                    break;
                }
                case UserRemove:
                {
                    var m = Pb.Parse(body);
                    int who = (int)m.Num(1, -1);
                    User gone;
                    _users.TryRemove(who, out gone);
                    if (who == _session)
                    {
                        Finish(new Closed { Reason = CloseReason.Kicked, Text = m.Text(3, "") });
                        throw new EndOfStreamException();
                    }
                    if (_synced) _listener.OnChanged();
                    break;
                }
                case PermissionDenied:
                {
                    var m = Pb.Parse(body);
                    _listener.OnDenied((int)m.Num(5, 0), m.Text(4, ""));
                    break;
                }
            }
        }

        /// <summary>The channel tree in order, the root first.</summary>
        public List<Channel> Tree()
        {
            var list = new List<Channel>();
            var root = GetChannel(0);
            if (root != null) AddTree(root, list);
            return list;
        }

        private void AddTree(Channel c, List<Channel> list)
        {
            list.Add(c);
            foreach (var s in _channels.Values.Where(x => x.Parent == c.Id && x.Id != c.Id)
                .OrderBy(x => x.Name, StringComparer.OrdinalIgnoreCase))
                AddTree(s, list);
        }
    }
}
