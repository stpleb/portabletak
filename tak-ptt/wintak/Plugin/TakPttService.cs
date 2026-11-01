using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using TakPtt.Voice;
using WinTak.Common.Preferences;
using WinTak.Common.Services;
using WinTak.Common.Utils;
using WinTak.CursorOnTarget.Placement.Settings;
using WinTak.Framework.Preferences;
using WinTak.Graphics.Map;
using WinTak.Net.Streaming;

namespace TakPtt.WinTak
{
    /// <summary>
    /// Voice for the whole of WinTAK: one engine, set up from the TAK server WinTAK is connected to
    /// with the certificate it enrolled there, as the ATAK plugin does. The pane comes and goes.
    /// </summary>
    internal static class TakPttService
    {
        private static ICommunicationService _communication;
        private static IDevicePreferences _device;
        private static CoTSettings _settings;
        private static string _activeServer;
        private static VoiceLogin _login;
        private static readonly object Lock = new object();

        public static VoiceEngine Engine { get; private set; }

        public static VoiceHistory History { get; private set; }

        public static HistoryMarkers Markers { get; private set; }

        public static VoiceState Latest { get; private set; }

        public static event Action<VoiceState> Changed;

        public static void Start(ICommunicationService communication, IDevicePreferences device,
            IEnumerable<Lazy<INetworkPreference>> networkPreferences, IMapGroupManager mapGroups, ILocationService location, global::WinTak.Display.IMapViewController map)
        {
            _communication = communication;
            _device = device;
            _settings = FindSettings(networkPreferences);
            var prefs = new FilePrefs();
            Engine = new VoiceEngine(new DesktopAudio(), prefs, s =>
            {
                Latest = s;
                var history = History;
                if (history != null) history.Live = s.Talking || s.Receiving != null;   // live traffic always wins
                Changed?.Invoke(s);
            });
            string clips = Path.Combine(System.Environment.GetFolderPath(System.Environment.SpecialFolder.LocalApplicationData), "TakPtt", "clips");
            History = new VoiceHistory(new HistoryClient(clips), prefs, Engine.Output) { Login = () => _login, Transcriber = Speech(), Me = device?.Callsign };
            Engine.Position = () => PositionOf(location);
            if (mapGroups != null) Markers = new HistoryMarkers(mapGroups, History, map);
            _communication.Connected += OnConnected;
            UseTakServer();
        }

        public static void Stop()
        {
            if (_communication != null) _communication.Connected -= OnConnected;
            Engine?.Shutdown();
            Engine = null;
        }

        /// <summary>Whisper from the plugin's folder, with Windows' own recognizer behind it.</summary>
        private static ITranscriber Speech()
        {
            string dir = Path.Combine(Path.GetDirectoryName(typeof(TakPttService).Assembly.Location), "whisper");
            string program = Path.Combine(dir, "TakPtt.Whisper.exe"), model = Path.Combine(dir, "ggml-base.en.bin");
            var windows = DesktopTranscriber.Create();
            return new WhisperTranscriber(program, "", model, windows)
            {
                Problem = text =>
                {
                    var history = History;
                    if (history == null || history.TextProblem == text) return;
                    history.TextProblem = text;
                }
            };
        }

        /// <summary>Our place from the self position CoT WinTAK sends, which also covers a location set by hand.</summary>
        private static double[] PositionOf(ILocationService location)
        {
            try
            {
                var point = location?.GetPositionDocument()?.SelectSingleNode("//point");
                double lat, lon;
                var us = System.Globalization.CultureInfo.InvariantCulture;
                if (point != null && double.TryParse(point.Attributes["lat"]?.Value, System.Globalization.NumberStyles.Float, us, out lat)
                    && double.TryParse(point.Attributes["lon"]?.Value, System.Globalization.NumberStyles.Float, us, out lon)
                    && (lat != 0 || lon != 0))
                    return new[] { lat, lon };
            }
            catch (Exception)
            {
                // no position: the clip just has no place
            }
            return null;
        }

        /// <summary>The connections' certificates are on WinTAK's network settings object.</summary>
        private static CoTSettings FindSettings(IEnumerable<Lazy<INetworkPreference>> preferences)
        {
            foreach (var p in preferences)
            {
                try
                {
                    var s = p.Value as CoTSettings;
                    if (s != null) return s;
                }
                catch (Exception)
                {
                    // another plugin's preference failed to load: not ours to report
                }
            }
            return null;
        }

        private static void OnConnected(object sender, EventArgs e) { UseTakServer(); }

        /// <summary>
        /// A store's possible passwords. WinTAK makes its own password when it enrolls and keeps it encrypted
        /// (Windows DPAPI, this user) in a file beside the certificate. StreamConnection only holds it
        /// right after enrolling: a restart leaves ClientPassword empty. The file's suffix is hidden in
        /// WinTAK, so every file beside the store is tried.
        /// </summary>
        private static List<string> SavedPasswords(string known, string store)
        {
            var found = new List<string> { known };
            if (store == null) return found;
            foreach (var file in Directory.GetFiles(Path.GetDirectoryName(store), Path.GetFileName(store) + "*"))
            {
                if (file == store) continue;
                try
                {
                    string p = EncryptionUtils.UnprotectPasswordFromFile(file);
                    if (!string.IsNullOrEmpty(p)) found.Add(p);
                }
                catch (Exception)
                {
                    // not a password file
                }
            }
            return found;
        }

        /// <summary>The TAK server WinTAK is connected to (or, when none is connected, the first one set up).</summary>
        private static void UseTakServer()
        {
            lock (Lock)
            {
                if (Engine == null) return;
                if (_settings == null)
                {
                    Engine.Problem("Could not read WinTAK's server connections");
                    return;
                }
                var servers = _settings.ServerConnections.ToList();
                var server = servers.FirstOrDefault(s => s.Connected) ?? servers.FirstOrDefault(s => s.Enabled) ?? servers.FirstOrDefault();
                if (server == null)
                {
                    Engine.Problem("Connect WinTAK to your PortableTAK server first");
                    return;
                }
                if (server.ConnectionString == _activeServer) return;
                string name = string.IsNullOrEmpty(server.Description) ? server.HostAddress : server.Description;
                try
                {
                    string client = VoiceLogin.FindStore(server.ClientCertificatePath, server.ClientCertificateName);
                    string trust = VoiceLogin.FindStore(server.ServerCertificatePath, server.ServerCertificateName);
                    var login = VoiceLogin.Create(name, server.HostAddress, VoiceLogin.DefaultPort,
                        client == null ? null : File.ReadAllBytes(client), SavedPasswords(server.ClientPassword, client),
                        trust == null ? null : File.ReadAllBytes(trust), SavedPasswords(server.ServerPassword, trust));
                    _activeServer = server.ConnectionString;
                    _login = login;
                    Engine.Connect(login, _device?.Callsign);
                }
                catch (SetupException e)
                {
                    Engine.Problem(e.Message);
                }
                catch (IOException e)
                {
                    Engine.Problem("Could not read the TAK certificate: " + e.Message);
                }
            }
        }
    }

    /// <summary>Settings in a small text file, one "key=value" a line.</summary>
    internal sealed class FilePrefs : IPrefs
    {
        private readonly string _path = Path.Combine(System.Environment.GetFolderPath(System.Environment.SpecialFolder.ApplicationData), "TakPtt", "settings.txt");
        private readonly Dictionary<string, string> _values = new Dictionary<string, string>();

        public FilePrefs()
        {
            try
            {
                if (!File.Exists(_path)) return;
                foreach (var line in File.ReadAllLines(_path))
                {
                    int eq = line.IndexOf('=');
                    if (eq > 0) _values[line.Substring(0, eq)] = line.Substring(eq + 1).Replace("\\n", "\n");
                }
            }
            catch (IOException)
            {
                // starts with the defaults
            }
        }

        public string Get(string key, string otherwise)
        {
            string v;
            lock (_values) return _values.TryGetValue(key, out v) ? v : otherwise;
        }

        public void Put(string key, string value)
        {
            lock (_values)
            {
                _values[key] = value;
                try
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(_path));
                    File.WriteAllLines(_path, _values.Select(p => p.Key + "=" + p.Value.Replace("\n", "\\n")));
                }
                catch (IOException)
                {
                    // kept for this run
                }
            }
        }
    }
}
