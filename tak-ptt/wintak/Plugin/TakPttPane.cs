using System.ComponentModel.Composition;
using WinTak.Framework.Docking;
using WinTak.Framework.Docking.Attributes;
using WinTak.Framework.Tools;
using WinTak.Framework.Tools.Attributes;

namespace TakPtt.WinTak
{
    [DockPane(Id, "TAK PTT", Content = typeof(PttView))]
    internal sealed class TakPttDockPane : DockPane
    {
        internal const string Id = "TakPtt_DockPanes_TakPttDockPane";

        [ImportingConstructor]
        public TakPttDockPane()
        {
        }
    }

    [Button("TakPtt_Buttons_TakPttButton", "TAK PTT",
        LargeImage = "pack://application:,,,/TakPtt.WinTak;component/Assets/takptt_32.png",
        SmallImage = "pack://application:,,,/TakPtt.WinTak;component/Assets/takptt_16.png")]
    internal sealed class TakPttButton : Button
    {
        private readonly IDockingManager _dockingManager;

        [ImportingConstructor]
        public TakPttButton(IDockingManager dockingManager)
        {
            _dockingManager = dockingManager;
        }

        protected override void OnClick()
        {
            base.OnClick();
            _dockingManager.GetDockPane(TakPttDockPane.Id)?.Activate();
        }
    }
}
