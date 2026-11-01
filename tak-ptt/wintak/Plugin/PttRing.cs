using System;
using System.Globalization;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace TakPtt.WinTak
{
    /// <summary>TAK PTT colours: green theme on a near-black background, as in the ATAK plugin.</summary>
    internal static class Theme
    {
        public static readonly Color Bg = Rgb(0x050A07), Panel = Rgb(0x09110C), PanelDark = Rgb(0x07100F), Here = Rgb(0x0E2015),
            Line = Rgb(0x1F5C3A), Green = Rgb(0x32E07A), Red = Rgb(0xFF4D4D), Amber = Rgb(0xFFB84D), Muted = Rgb(0x4E5C55),
            Text = Rgb(0xDDEBE2), Sub = Rgb(0x8FA79A);

        private static Color Rgb(int c) { return Color.FromRgb((byte)(c >> 16), (byte)(c >> 8), (byte)c); }

        public static SolidColorBrush Brush(Color c)
        {
            var b = new SolidColorBrush(c);
            b.Freeze();
            return b;
        }
    }

    /// <summary>
    /// The PTT button, the same design as the ATAK plugin's: a ring of rounded segments around a circle
    /// with "PTT" and the state. Ready = green, talking = red with the ring pulsing, receiving = amber
    /// with the ring sweeping, off = grey.
    /// </summary>
    internal sealed class PttRing : FrameworkElement
    {
        private const int Segments = 36;
        private Color _colour = Theme.Muted;
        private string _state = "";
        private bool _animate, _held;
        private readonly DispatcherTimer _ticks = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(40) };

        public event Action Down, Up;

        public PttRing()
        {
            _ticks.Tick += (s, e) => InvalidateVisual();
            Unloaded += (s, e) => _ticks.Stop();
            Loaded += (s, e) => { if (_animate) _ticks.Start(); };
        }

        /// <summary>Colour of the ring and text, the line under "PTT", and whether the ring moves.</summary>
        public void Show(Color colour, string state, bool animate)
        {
            _colour = colour;
            _state = state;
            _animate = animate;
            if (animate) _ticks.Start(); else _ticks.Stop();
            InvalidateVisual();
        }

        protected override Size MeasureOverride(Size available)
        {
            double s = Math.Min(Math.Min(available.Width, available.Height), 360);
            if (double.IsInfinity(s)) s = 240;
            return new Size(s, s);
        }

        protected override void OnMouseLeftButtonDown(MouseButtonEventArgs e)
        {
            var p = e.GetPosition(this);
            if (Math.Sqrt(Math.Pow(p.X - ActualWidth / 2, 2) + Math.Pow(p.Y - ActualHeight / 2, 2)) > Math.Min(ActualWidth, ActualHeight) / 2) return;
            Focus();
            CaptureMouse();
            _held = true;
            Down?.Invoke();
            InvalidateVisual();
            e.Handled = true;
        }

        protected override void OnMouseLeftButtonUp(MouseButtonEventArgs e)
        {
            ReleaseMouseCapture();
        }

        protected override void OnLostMouseCapture(MouseEventArgs e)
        {
            if (!_held) return;
            _held = false;
            Up?.Invoke();
            InvalidateVisual();
        }

        private static Color Alpha(Color c, int a) { return Color.FromArgb((byte)Math.Max(0, Math.Min(255, a)), c.R, c.G, c.B); }

        protected override void OnRender(DrawingContext g)
        {
            // Hit-testing covers the square, so a press in a corner reaches OnMouseLeftButtonDown to be refused.
            g.DrawRectangle(Brushes.Transparent, null, new Rect(0, 0, ActualWidth, ActualHeight));
            double s = Math.Min(ActualWidth, ActualHeight), cx = ActualWidth / 2, cy = ActualHeight / 2;
            if (s <= 0) return;
            // Everything is a fraction of the size: thick segmented ring, inner ring at 78% of it, text inside.
            double segW = s * 0.06, outer = s / 2 - segW / 2 - s * 0.01, inner = outer * 0.78;
            bool off = _colour == Theme.Muted;
            double phase = (DateTime.UtcNow.Ticks / TimeSpan.TicksPerMillisecond % 1200) / 1200.0;
            var centre = new Point(cx, cy);
            g.DrawEllipse(Theme.Brush(Alpha(_colour, _held ? 60 : 22)), null, centre, inner + s * 0.02, inner + s * 0.02);
            g.DrawEllipse(Theme.Brush(Theme.PanelDark), null, centre, inner - s * 0.01, inner - s * 0.01);
            double sweep = 360.0 / Segments, gap = 360 * (segW * 1.15) / (2 * Math.PI * outer);
            for (int i = 0; i < Segments; i++)
            {
                int a;
                if (!_animate) a = off ? 90 : 220;
                else if (_colour == Theme.Red) a = (int)(150 + 105 * Math.Abs(Math.Sin(Math.PI * phase * 2)));     // pulse
                else a = (int)(60 + 195 * Math.Pow(1 - (((double)i / Segments - phase + 1) % 1.0), 3));             // sweep
                var pen = new Pen(Theme.Brush(Alpha(_colour, a)), segW) { StartLineCap = PenLineCap.Round, EndLineCap = PenLineCap.Round };
                double from = (-90 + i * sweep + gap / 2) * Math.PI / 180, to = (-90 + i * sweep + Math.Max(1, sweep - gap / 2)) * Math.PI / 180;
                var geometry = new StreamGeometry();
                using (var c = geometry.Open())
                {
                    c.BeginFigure(new Point(cx + outer * Math.Cos(from), cy + outer * Math.Sin(from)), false, false);
                    c.ArcTo(new Point(cx + outer * Math.Cos(to), cy + outer * Math.Sin(to)), new Size(outer, outer), 0, false, SweepDirection.Clockwise, true, false);
                }
                geometry.Freeze();
                g.DrawGeometry(null, pen, geometry);
            }
            g.DrawEllipse(null, new Pen(Theme.Brush(Alpha(_colour, off ? 120 : 255)), Math.Max(2, s * 0.014)), centre, inner, inner);
            Text(g, "PTT", new Typeface(new FontFamily("Segoe UI"), FontStyles.Normal, FontWeights.Bold, FontStretches.Normal), inner * 0.5, off ? Theme.Sub : _colour, cx, cy - inner * 0.3, inner * 1.6);
            Text(g, _state, new Typeface("Consolas"), Math.Max(9, inner * 0.13), Theme.Sub, cx, cy + inner * 0.32, inner * 1.5);
        }

        private void Text(DrawingContext g, string text, Typeface face, double size, Color colour, double cx, double top, double maxWidth)
        {
            var f = new FormattedText(text, CultureInfo.CurrentUICulture, FlowDirection.LeftToRight, face, size, Theme.Brush(colour),
                VisualTreeHelper.GetDpi(this).PixelsPerDip);
            if (f.Width > maxWidth) f.SetFontSize(size * maxWidth / f.Width);       // keep the state line inside the ring
            g.DrawText(f, new Point(cx - f.Width / 2, top));
        }
    }
}
