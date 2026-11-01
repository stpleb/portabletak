using System;
using System.ComponentModel.Composition;
using System.IO;
using System.Reflection;
using Prism.Mef.Modularity;
using Prism.Modularity;
using WinTak.Common.Preferences;
using WinTak.Common.Services;
using WinTak.Framework;
using WinTak.Framework.Preferences;
using WinTak.Graphics.Map;
using System.Collections.Generic;

namespace TakPtt.WinTak
{
    /// <summary>Starts voice with WinTAK, so traffic is heard without opening the pane.</summary>
    [ModuleExport(typeof(TakPttModule), InitializationMode = InitializationMode.WhenAvailable)]
    internal sealed class TakPttModule : IModule, ITakModule
    {
        private readonly ICommunicationService _communication;
        private readonly IDevicePreferences _device;
        private readonly IEnumerable<Lazy<INetworkPreference>> _networkPreferences;
        private readonly IMapGroupManager _mapGroups;
        private readonly ILocationService _location;

        // Libraries beside the plugin DLL are found here: WinTAK loads the plugin itself by path.
        static TakPttModule()
        {
            string dir = Path.GetDirectoryName(typeof(TakPttModule).Assembly.Location);
            AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
            {
                string file = Path.Combine(dir, new AssemblyName(e.Name).Name + ".dll");
                return File.Exists(file) ? Assembly.LoadFrom(file) : null;
            };
        }

        [ImportingConstructor]
        public TakPttModule(ICommunicationService communication,
            [Import(AllowDefault = true)] IDevicePreferences device,
            [ImportMany] IEnumerable<Lazy<INetworkPreference>> networkPreferences,
            [Import(AllowDefault = true)] IMapGroupManager mapGroups,
            [Import(AllowDefault = true)] ILocationService location)
        {
            _communication = communication;
            _device = device;
            _networkPreferences = networkPreferences;
            _mapGroups = mapGroups;
            _location = location;
        }

        public void Initialize()
        {
        }

        /// <summary>Called when the WinTAK splash screen has closed and startup is finished.</summary>
        public void Startup()
        {
            TakPttService.Start(_communication, _device, _networkPreferences, _mapGroups, _location);
        }

        public void Terminate()
        {
            TakPttService.Stop();
        }
    }
}
