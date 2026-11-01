using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;
using TakPtt.Voice;
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

        public HistoryMarkers(IMapGroupManager groups, VoiceHistory history)
        {
            _history = history;
            _group = groups.GetOrCreateMapGroup(Group);
            history.Changed += () => Application.Current?.Dispatcher.BeginInvoke(new Action(Update));
            Application.Current?.Dispatcher.BeginInvoke(new Action(Update));
        }

        private void Update()
        {
            var want = _history.MapOn ? _history.Clips.Where(c => c.Lat != null && c.Lon != null).ToDictionary(c => c.Id) : new Dictionary<long, Clip>();
            foreach (var id in _shown.Keys.Where(k => !want.ContainsKey(k)).ToList())
            {
                _group.RemoveItem(_shown[id]);
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
                marker.IsSelectedChanged += (s, e) => { if (marker.IsSelected) _history.Play(clip); };
                _group.AddItem(marker);
                _shown[clip.Id] = marker;
            }
        }
    }
}
