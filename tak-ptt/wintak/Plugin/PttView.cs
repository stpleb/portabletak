using System;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using TakPtt.Voice;

namespace TakPtt.WinTak
{
    /// <summary>
    /// The TAK PTT pane: status, the PTT ring (mouse, or Space while the pane has focus), the channel
    /// list with RX and TX picks, and the settings. Written in code so it builds without XAML tools.
    /// </summary>
    public sealed class PttView : UserControl
    {
        private readonly TextBlock _status = Line(11, Theme.Text), _detail = Line(11, Theme.Sub);
        private static readonly string[] TabNames = { "PTT", "CHANNELS", "HISTORY" };
        private readonly PttRing _ring = new PttRing { Focusable = true, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 6, 0, 6) };
        private readonly UniformGrid _tabBar = new UniformGrid { Rows = 1, Margin = new Thickness(0, 6, 0, 8) };
        private readonly HistoryPage _history = new HistoryPage();
        private readonly FrameworkElement[] _pages;
        private readonly StackPanel _channels = new StackPanel();
        private readonly ComboBox _mode = new ComboBox { ItemsSource = new[] { "Hold to talk", "Tap on, tap off" }, MinWidth = 130 };
        private readonly ComboBox _timeout = new ComboBox { ItemsSource = Enumerable.Range(2, 8).Select(i => i * 10).ToArray(), MinWidth = 70 };
        private readonly CheckBox _tones = Check("Tones"), _rejoin = Check("Go back to my last channel");
        private readonly StackPanel _recent = new StackPanel { Margin = new Thickness(0, 6, 0, 0) };
        private string _shownRows = "", _shownRecent = "";
        private int _tab, _unheard;
        private bool _wasLive;
        private bool _updating, _spaceHeld;

        private static VoiceEngine Engine { get { return TakPttService.Engine; } }

        public PttView()
        {
            Background = Theme.Brush(Theme.Bg);
            Focusable = true;
            var root = new DockPanel { Margin = new Thickness(8), LastChildFill = true };

            var top = new StackPanel();
            top.Children.Add(_status);
            top.Children.Add(_detail);
            top.Children.Add(_tabBar);
            DockPanel.SetDock(top, Dock.Top);
            root.Children.Add(top);

            var talk = new DockPanel();
            var settings = new StackPanel { Margin = new Thickness(0, 6, 0, 0) };
            settings.Children.Add(Labelled("Talk", _mode));
            settings.Children.Add(Labelled("Time limit (s)", _timeout));
            settings.Children.Add(_tones);
            settings.Children.Add(_rejoin);
            DockPanel.SetDock(_recent, Dock.Bottom);
            talk.Children.Add(_recent);
            DockPanel.SetDock(settings, Dock.Bottom);
            talk.Children.Add(settings);
            talk.Children.Add(_ring);

            _pages = new FrameworkElement[]
            {
                talk,
                new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = _channels },
                _history
            };
            var pages = new Grid();
            foreach (var page in _pages) pages.Children.Add(page);
            root.Children.Add(pages);
            Content = root;
            ShowTab(0);
            _history.Shown += ShowRecent;

            _ring.Down += () => Engine?.PttDown();
            _ring.Up += () => Engine?.PttUp();
            PreviewKeyDown += (s, e) =>
            {
                if (e.Key != Key.Space || e.IsRepeat || e.OriginalSource is TextBox) return;
                e.Handled = true;
                if (_spaceHeld) return;
                _spaceHeld = true;
                Engine?.PttDown();
            };
            PreviewKeyUp += (s, e) =>
            {
                if (e.Key != Key.Space || !_spaceHeld) return;
                e.Handled = true;
                _spaceHeld = false;
                Engine?.PttUp();
            };
            LostKeyboardFocus += (s, e) =>
            {
                if (!_spaceHeld || IsKeyboardFocusWithin) return;
                _spaceHeld = false;                         // the key-up will go elsewhere
                Engine?.PttUp();
            };
            _mode.SelectionChanged += (s, e) => { if (!_updating) Engine?.SetMode(_mode.SelectedIndex == 1 ? VoiceEngine.ModeLatch : VoiceEngine.ModeHold); };
            _timeout.SelectionChanged += (s, e) => { if (!_updating && _timeout.SelectedItem != null) Engine?.SetTimeout((int)_timeout.SelectedItem); };
            _tones.Click += (s, e) => Engine?.SetTones(_tones.IsChecked == true);
            _rejoin.Click += (s, e) => Engine?.SetRejoin(_rejoin.IsChecked == true);

            Loaded += (s, e) =>
            {
                TakPttService.Changed += OnChanged;
                if (TakPttService.Latest != null) ShowState(TakPttService.Latest);
                else _ring.Show(Theme.Muted, "OFFLINE", false);
            };
            Unloaded += (s, e) => TakPttService.Changed -= OnChanged;
        }

        private void ShowTab(int tab)
        {
            for (int i = 0; i < _pages.Length; i++)
                _pages[i].Visibility = i == tab ? Visibility.Visible : Visibility.Collapsed;
            _tab = tab;
            ShowTabBar();
            Focus();                                        // so Space talks from any tab
            if (tab == 2) _history.Update();
        }

        private void ShowTabBar()
        {
            _tabBar.Children.Clear();
            for (int i = 0; i < TabNames.Length; i++)
            {
                int n = i;
                var cell = new Grid();
                cell.Children.Add(Toggle(TabNames[i], i == _tab, false, Theme.Green, () => ShowTab(n)));
                if (i == 2 && _unheard > 0) cell.Children.Add(Badge(_unheard));
                _tabBar.Children.Add(cell);
            }
        }

        /// <summary>The small count of unheard transmissions on the HISTORY tab.</summary>
        private static UIElement Badge(int count)
        {
            return new Border
            {
                Background = Theme.Brush(Theme.Red), CornerRadius = new CornerRadius(8), MinWidth = 16, Height = 16,
                Padding = new Thickness(4, 0, 4, 0), Margin = new Thickness(0, -6, -4, 0), IsHitTestVisible = false,
                HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Top,
                Child = new TextBlock
                {
                    Text = count > 99 ? "99+" : count.ToString(), FontSize = 10, FontWeight = FontWeights.Bold,
                    Foreground = Brushes.White, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center
                }
            };
        }

        /// <summary>The badge, and the last five transmissions under the PTT ring. Click one to play it.</summary>
        private void ShowRecent()
        {
            var h = TakPttService.History;
            if (h == null) return;
            int unheard = h.UnheardCount;
            if (unheard != _unheard)
            {
                _unheard = unheard;
                ShowTabBar();
            }
            var clips = h.Clips.Take(5).ToList();
            string key = string.Join("\n", clips.Select(c => c.Id + "|" + h.Unheard(c)));
            if (key == _shownRecent) return;
            _shownRecent = key;
            _recent.Children.Clear();
            if (clips.Count > 0)
                _recent.Children.Add(new TextBlock { Text = "RECENT", FontFamily = new FontFamily("Consolas"), FontSize = 10, Foreground = Theme.Brush(Theme.Sub), Margin = new Thickness(0, 0, 0, 2) });
            foreach (var c in clips)
            {
                var clip = c;
                var when = DateTimeOffset.FromUnixTimeMilliseconds((long)(c.Start * 1000)).LocalDateTime;
                var row = new DockPanel();
                var time = new TextBlock { Text = when.ToString("HH:mm") + " · " + c.Duration.ToString("F0") + " s", FontFamily = new FontFamily("Consolas"), FontSize = 10, Foreground = Theme.Brush(Theme.Sub), VerticalAlignment = VerticalAlignment.Center };
                DockPanel.SetDock(time, Dock.Right);
                row.Children.Add(time);
                row.Children.Add(new TextBlock
                {
                    Text = (h.Unheard(c) ? "● " : "") + c.Sender + " · " + c.Channel, FontSize = 11, TextTrimming = TextTrimming.CharacterEllipsis,
                    Foreground = Theme.Brush(h.Unheard(c) ? Theme.Amber : Theme.Text), Margin = new Thickness(0, 0, 6, 0)
                });
                var box = new Border
                {
                    Background = Theme.Brush(Theme.Panel), BorderBrush = Theme.Brush(Theme.Line), BorderThickness = new Thickness(1),
                    CornerRadius = new CornerRadius(3), Padding = new Thickness(6, 2, 6, 2), Margin = new Thickness(0, 0, 0, 2), Child = row, Cursor = Cursors.Hand
                };
                box.MouseLeftButtonUp += (s, e) => TakPttService.History?.Play(clip);
                _recent.Children.Add(box);
            }
        }

        private void OnChanged(VoiceState s) { Dispatcher.BeginInvoke(new Action(() => ShowState(s))); }

        private static TextBlock Line(double size, Color colour)
        {
            return new TextBlock
            {
                FontFamily = new FontFamily("Consolas"), FontSize = size, Foreground = Theme.Brush(colour),
                TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center
            };
        }

        private static CheckBox Check(string text)
        {
            return new CheckBox { Content = text, Foreground = Theme.Brush(Theme.Text), Margin = new Thickness(0, 3, 0, 3) };
        }

        private static UIElement Labelled(string label, UIElement control)
        {
            var row = new DockPanel { Margin = new Thickness(0, 2, 0, 2) };
            var text = new TextBlock { Text = label, Foreground = Theme.Brush(Theme.Sub), VerticalAlignment = VerticalAlignment.Center, Width = 100 };
            row.Children.Add(text);
            row.Children.Add(control);
            return row;
        }

        private void ShowState(VoiceState s)
        {
            bool live = s.Talking || s.Receiving != null;
            if (_wasLive && !live) _history.UpdateSoon();
            _wasLive = live;
            if (s.Talking) _ring.Show(Theme.Red, "TRANSMITTING", true);
            else if (s.Receiving != null) _ring.Show(Theme.Amber, s.Receiving, true);
            else if (s.Connected) _ring.Show(Theme.Green, s.Mode == VoiceEngine.ModeLatch ? "TAP TO TALK" : "HOLD TO TALK", false);
            else _ring.Show(Theme.Muted, s.Connecting ? "CONNECTING" : "OFFLINE", false);
            _status.Text = s.Connected ? s.ChannelName + " · " + s.ServerName : s.Connecting ? "Connecting to " + s.ServerName : "Voice off";
            string line = s.Note ?? s.Error;
            if (line != null) _detail.Text = line;
            else if (s.Connected) _detail.Text = "";
            _updating = true;
            _mode.SelectedIndex = s.Mode == VoiceEngine.ModeLatch ? 1 : 0;
            _timeout.SelectedItem = s.Timeout / 10 * 10;
            _tones.IsChecked = s.TonesOn;
            _rejoin.IsChecked = s.Rejoin;
            _updating = false;
            string key = string.Join("\n", s.Rows.Select(r => r.Key));
            if (key != _shownRows)
            {
                _shownRows = key;
                Rows(s);
            }
        }

        private void Rows(VoiceState s)
        {
            _channels.Children.Clear();
            foreach (var r in s.Rows)
            {
                var row = new DockPanel();
                var picks = new StackPanel { Orientation = Orientation.Horizontal };
                var row1 = r;
                picks.Children.Add(Toggle("RX", r.Rx, r.Here, r.Active && r.Rx ? Theme.Amber : Theme.Green, () => Engine?.SetRx(row1.Id, !row1.Rx)));
                picks.Children.Add(Toggle("TX", r.Tx, false, Theme.Red, () => Engine?.SetTx(row1.Id, !row1.Tx)));
                DockPanel.SetDock(picks, Dock.Right);
                row.Children.Add(picks);
                var names = new StackPanel { Background = Brushes.Transparent, Cursor = Cursors.Hand };
                names.Children.Add(new TextBlock
                {
                    Text = (r.Active ? "● " : "") + r.Name, FontWeight = FontWeights.Bold,
                    Foreground = Theme.Brush(r.Active ? Theme.Amber : r.Here ? Theme.Green : Theme.Text),
                    TextTrimming = TextTrimming.CharacterEllipsis
                });
                names.Children.Add(new TextBlock
                {
                    Text = (r.Here ? "HERE · " : "") + r.People + (r.People == 1 ? " person" : " people"),
                    FontFamily = new FontFamily("Consolas"), FontSize = 10, Foreground = Theme.Brush(Theme.Sub)
                });
                names.MouseLeftButtonUp += (o, e) => Engine?.Join(row1.Id);
                row.Children.Add(names);
                _channels.Children.Add(new Border
                {
                    Background = Theme.Brush(r.Here ? Theme.Here : Theme.Panel), BorderBrush = Theme.Brush(r.Here ? Theme.Green : Theme.Line),
                    BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(4), Padding = new Thickness(8, 4, 4, 4),
                    Margin = new Thickness(0, 0, 0, 4), Child = row
                });
            }
            if (s.Connected) _channels.Children.Add(NewChannel());
        }

        private static UIElement NewChannel()
        {
            var row = new DockPanel();
            var name = new TextBox { Background = Theme.Brush(Theme.Panel), Foreground = Theme.Brush(Theme.Text), CaretBrush = Theme.Brush(Theme.Text), BorderBrush = Theme.Brush(Theme.Line), Padding = new Thickness(4) };
            Action make = () =>
            {
                string text = name.Text.Trim();
                if (text.Length > 0) Engine?.Create(text);
                name.Clear();
            };
            name.KeyDown += (s, e) => { if (e.Key == Key.Return) make(); };
            var add = Toggle("+ NEW", true, false, Theme.Green, make);
            DockPanel.SetDock(add, Dock.Right);
            row.Children.Add(add);
            row.Children.Add(name);
            return row;
        }

        internal static UIElement Toggle(string text, bool on, bool locked, Color colour, Action click)
        {
            var label = new TextBlock
            {
                Text = text, FontFamily = new FontFamily("Consolas"), FontWeight = FontWeights.Bold, FontSize = 11,
                Foreground = Theme.Brush(on ? colour : Theme.Muted), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center
            };
            var box = new Border
            {
                Background = Brushes.Transparent, BorderBrush = Theme.Brush(on ? colour : Theme.Line), BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(3), Padding = new Thickness(8, 4, 8, 4), Margin = new Thickness(4, 0, 0, 0), Child = label
            };
            if (!locked)                                    // the channel you are in is always heard
            {
                box.Cursor = Cursors.Hand;
                box.MouseLeftButtonUp += (s, e) => click();
            }
            return box;
        }
    }
}
