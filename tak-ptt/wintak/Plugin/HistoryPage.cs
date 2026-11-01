using System;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Threading;
using TakPtt.Voice;

namespace TakPtt.WinTak
{
    /// <summary>
    /// The HISTORY tab: recent transmissions, newest first, with who, on which channel, when and for
    /// how long; unheard ones have a dot. Click to play; the bar below has play/pause and a scrub bar,
    /// and PLAY UNHEARD plays the unheard ones in order, oldest first.
    /// </summary>
    internal sealed class HistoryPage : DockPanel
    {
        private static readonly TimeSpan RefreshEvery = TimeSpan.FromSeconds(20);
        private readonly TextBlock _status = Small(Theme.Sub), _now = Small(Theme.Text), _time = Small(Theme.Sub);
        private readonly StackPanel _list = new StackPanel();
        private readonly WrapPanel _buttons = new WrapPanel();
        private readonly Slider _scrub = new Slider { Minimum = 0, IsMoveToPointEnabled = true, VerticalAlignment = VerticalAlignment.Center };
        private readonly ContentControl _pause = new ContentControl();
        private readonly DockPanel _player = new DockPanel { Visibility = Visibility.Collapsed, Margin = new Thickness(0, 6, 0, 0) };
        private readonly DispatcherTimer _ticks = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(250) };
        private DateTime _lastRefresh;
        private string _shownRows = "", _shownButtons = "";
        private bool _dragging, _pauseShowsPlay, _pauseDrawn;

        private static VoiceHistory History { get { return TakPttService.History; } }

        public HistoryPage()
        {
            LastChildFill = true;
            var bar = new StackPanel { Margin = new Thickness(0, 0, 0, 6) };
            bar.Children.Add(_buttons);
            _status.TextWrapping = TextWrapping.Wrap;
            _status.Margin = new Thickness(0, 4, 0, 0);
            bar.Children.Add(_status);
            SetDock(bar, Dock.Top);
            Children.Add(bar);
            ShowButtons();

            var line = new DockPanel();
            DockPanel.SetDock(_pause, Dock.Left);
            line.Children.Add(_pause);
            DockPanel.SetDock(_time, Dock.Right);
            _time.VerticalAlignment = VerticalAlignment.Center;
            _time.Margin = new Thickness(6, 0, 0, 0);
            line.Children.Add(_time);
            _scrub.Margin = new Thickness(6, 0, 0, 0);
            line.Children.Add(_scrub);
            DockPanel.SetDock(_now, Dock.Top);
            _now.TextTrimming = TextTrimming.CharacterEllipsis;
            _player.Children.Add(_now);
            _player.Children.Add(line);
            SetDock(_player, Dock.Bottom);
            Children.Add(_player);

            Children.Add(new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = _list });

            _scrub.AddHandler(Thumb.DragStartedEvent, new DragStartedEventHandler((s, e) => _dragging = true));
            _scrub.AddHandler(Thumb.DragCompletedEvent, new DragCompletedEventHandler((s, e) => { _dragging = false; Seek(); }));
            _scrub.PreviewMouseLeftButtonUp += (s, e) => { if (!_dragging) Seek(); };
            _ticks.Tick += (s, e) => Tick();
            Loaded += (s, e) => { if (History != null) History.Changed += OnChanged; _ticks.Start(); ShowList(); };
            Unloaded += (s, e) => { if (History != null) History.Changed -= OnChanged; _ticks.Stop(); };
        }

        private static TextBlock Small(Color colour)
        {
            return new TextBlock { FontFamily = new FontFamily("Consolas"), FontSize = 11, Foreground = Theme.Brush(colour) };
        }

        private void ShowButtons()
        {
            var h = History;
            bool map = h != null && h.MapOn, text = h != null && h.TextOn;
            _buttons.Children.Clear();
            _buttons.Children.Add(PttView.Toggle("UNHEARD", true, false, Theme.Green, () => History?.PlayUnheard()));
            _buttons.Children.Add(PttView.Toggle(map ? "MAP ON" : "MAP", map, false, Theme.Green, () => { if (History != null) History.MapOn = !History.MapOn; }));
            _buttons.Children.Add(PttView.Toggle(text ? "TEXT ON" : "TEXT", text, false, Theme.Green, ToggleText));
            _buttons.Children.Add(PttView.Toggle("CLEAR", true, false, Theme.Green, AskClear));
            _buttons.Children.Add(PttView.Toggle("REFRESH", true, false, Theme.Green, Update));
        }

        private void ToggleText()
        {
            if (History == null) return;
            if (History.Transcriber == null)
            {
                _status.Text = "Windows has no speech recognizer for this language";
                return;
            }
            History.TextOn = !History.TextOn;
        }

        private void AskClear()
        {
            if (History == null || History.Clips.Count == 0) return;
            if (MessageBox.Show("Removes the listed transmissions and saved recordings from this computer. "
                + "The voice server still keeps them for everyone else.", "Clear history?", MessageBoxButton.OKCancel, MessageBoxImage.Question) == MessageBoxResult.OK)
                History.Clear();
        }

        private void OnChanged() { Dispatcher.BeginInvoke(new Action(ShowList)); }

        /// <summary>The list may have changed.</summary>
        public event Action Shown;

        /// <summary>A transmission just ended: look again once the recorder has closed its clip.</summary>
        public void UpdateSoon() { _lastRefresh = DateTime.UtcNow - RefreshEvery + TimeSpan.FromSeconds(3); }

        /// <summary>The tab was opened.</summary>
        public void Update()
        {
            _lastRefresh = DateTime.UtcNow;
            History?.Refresh();
        }

        private void Seek()
        {
            History?.Player.Seek(_scrub.Value);
            History?.Kick();
        }

        private void Tick()
        {
            var h = History;
            if (h == null) return;
            if (DateTime.UtcNow - _lastRefresh > RefreshEvery) Update();     // on every tab: the badge and recent list use it
            ShowList();                                     // WinTAK can restore the pane before the service starts
            if (Visibility != Visibility.Visible) return;
            bool loaded = h.Player.Loaded && h.Current != null;
            _player.Visibility = loaded ? Visibility.Visible : Visibility.Collapsed;
            if (!loaded) return;
            if (!_dragging)
            {
                _scrub.Maximum = Math.Max(0.1, h.Player.Length);
                _scrub.Value = h.Player.Seconds;
            }
            _time.Text = Clock(h.Player.Seconds) + " / " + Clock(h.Player.Length);
            _now.Text = h.Current.Sender + " · " + h.Current.Channel;
            bool showPlay = !h.Player.Playing;
            if (!_pauseDrawn || showPlay != _pauseShowsPlay)
            {
                _pauseDrawn = true;
                _pauseShowsPlay = showPlay;
                _pause.Content = PttView.Toggle(showPlay ? "PLAY" : "PAUSE", true, false, Theme.Green, () => History?.Toggle());
            }
        }

        private static string Clock(double seconds)
        {
            int s = (int)seconds;
            return (s / 60) + ":" + (s % 60).ToString("00");
        }

        private void ShowButtonsIfChanged()
        {
            string key = History.MapOn + "|" + History.TextOn;
            if (key == _shownButtons) return;
            _shownButtons = key;
            ShowButtons();
        }

        private void ShowList()
        {
            var h = History;
            if (h == null) return;
            ShowButtonsIfChanged();
            int unheard = h.UnheardCount;
            Shown?.Invoke();
            _status.Text = (h.Status.Length > 0 ? h.Status : unheard + " unheard") + (h.TextProblem.Length > 0 ? "\n" + h.TextProblem : "");
            var clips = h.Clips;
            var current = h.Current;
            string key = string.Join("\n", clips.Select(c => c.Id + "|" + h.Unheard(c) + "|" + h.Text(c))) + "|" + current?.Id + "|" + h.TextOn;
            if (key == _shownRows) return;
            _shownRows = key;
            _list.Children.Clear();
            foreach (var c in clips)
            {
                var clip = c;
                bool playing = current != null && current.Id == c.Id;
                var row = new StackPanel { Background = Brushes.Transparent };
                row.Children.Add(new TextBlock
                {
                    Text = (h.Unheard(c) ? "● " : "") + c.Sender, FontWeight = FontWeights.Bold,
                    Foreground = Theme.Brush(h.Unheard(c) ? Theme.Amber : playing ? Theme.Green : Theme.Text),
                    TextTrimming = TextTrimming.CharacterEllipsis
                });
                var when = DateTimeOffset.FromUnixTimeMilliseconds((long)(c.Start * 1000)).LocalDateTime;
                var line = Small(Theme.Sub);
                line.FontSize = 10;
                line.Text = c.Channel + " · " + when.ToString("MMM d HH:mm") + " · " + c.Duration.ToString("F0") + " s" + (c.Lat != null ? " · has location" : "");
                row.Children.Add(line);
                string said = h.TextOn ? h.Text(c) : null;
                if (!string.IsNullOrEmpty(said))
                    row.Children.Add(new TextBlock { Text = said, Foreground = Theme.Brush(Theme.Text), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 2, 0, 0) });
                var box = new Border
                {
                    Background = Theme.Brush(playing ? Theme.Here : Theme.Panel), BorderBrush = Theme.Brush(playing ? Theme.Green : Theme.Line),
                    BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(4), Padding = new Thickness(8, 4, 8, 4),
                    Margin = new Thickness(0, 0, 0, 4), Child = row, Cursor = System.Windows.Input.Cursors.Hand
                };
                box.MouseLeftButtonUp += (s, e) => History?.Play(clip);
                _list.Children.Add(box);
            }
        }
    }
}
