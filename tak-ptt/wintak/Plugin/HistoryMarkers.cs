using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using System.Windows.Input;
using TakPtt.Voice;
using WinTak.Display;
using WinTak.Graphics;
using WinTak.Graphics.Map;

namespace TakPtt.WinTak
{
    /// <summary>
    /// Clips with a location as markers on this computer's map only (nothing is sent to anyone), in a
    /// large icon so they show without zooming in. Click a marker to play its clip.
    /// </summary>
    internal sealed class HistoryMarkers
    {
        private const string Group = "TAK PTT voice", Prefix = "takptt-clip-";
        private static readonly Uri Icon = new Uri("pack://application:,,,/TakPtt.WinTak;component/Assets/takptt_64.png");
        private readonly MapGroup _group;
        private readonly VoiceHistory _history;
        private readonly Dictionary<long, MapMarker> _shown = new Dictionary<long, MapMarker>();
        private readonly Dictionary<MapMarker, Clip> _clips = new Dictionary<MapMarker, Clip>();
        private DateTime _played;

        public HistoryMarkers(IMapGroupManager groups, VoiceHistory history, IMapViewController map)
        {
            _history = history;
            _group = groups.GetOrCreateMapGroup(Group);
            if (map != null) map.ItemClick += OnItemClick;
            history.Changed += () => Application.Current?.Dispatcher.BeginInvoke(new Action(Update));
            Application.Current?.Dispatcher.BeginInvoke(new Action(Update));
        }

        private void OnItemClick(object sender, MapMouseEventArgs e)
        {
            if (e.Button != MouseButton.Left) return;
            var hit = (e.MapItems ?? Enumerable.Empty<MapItem>()).Concat(new[] { e.Item as MapItem });
            foreach (var item in hit)
                for (var i = item; i != null; i = i.Parent)
                {
                    var marker = i as MapMarker;
                    if (marker != null && _clips.ContainsKey(marker))
                    {
                        Play(_clips[marker]);
                        return;
                    }
                }
        }

        /// <summary>One click can arrive twice, as a click and as the marker being selected.</summary>
        private void Play(Clip clip)
        {
            if (DateTime.UtcNow - _played < TimeSpan.FromSeconds(1)) return;
            _played = DateTime.UtcNow;
            _history.Play(clip);
        }

        private void Update()
        {
            var want = _history.MapOn ? _history.Clips.Where(c => c.Lat != null && c.Lon != null).ToDictionary(c => c.Id) : new Dictionary<long, Clip>();
            foreach (var id in _shown.Keys.Where(k => !want.ContainsKey(k)).ToList())
            {
                _group.RemoveItem(_shown[id]);
                _clips.Remove(_shown[id]);
                _shown.Remove(id);
            }
            foreach (var pair in want.Where(p => !_shown.ContainsKey(p.Key)))
            {
                var clip = pair.Value;
                var when = DateTimeOffset.FromUnixTimeMilliseconds((long)(clip.Start * 1000)).LocalDateTime;
                var marker = new MapMarker(Prefix + clip.Id)
                {
                    Name = clip.Sender + " · " + clip.Channel + " · " + when.ToString("MMM d HH:mm"),
                    Position = new TAKEngine.Core.GeoPoint(clip.Lat.Value, clip.Lon.Value),
                    IconPath = Icon,
                    IconSize = new System.Drawing.Size(40, 40)
                };
                marker.IsSelectedChanged += (s, e) => { if (marker.IsSelected) Play(clip); };
                _group.AddItem(marker);
                _shown[clip.Id] = marker;
                _clips[marker] = clip;
            }
        }
    }
}
