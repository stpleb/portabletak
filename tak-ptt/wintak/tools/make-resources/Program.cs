using System.IO;
using System.Resources;

// Packs the icons as the assembly's WPF resources (what a XAML build calls TakPtt.WinTak.g.resources), so
// pack:// addresses work and the plugin builds without WPF's XAML tools, on any computer. Rerun if an icon changes.
//   dotnet run --project tools/make-resources -- Plugin/Assets
var dir = args[0];
using var w = new ResourceWriter(Path.Combine(dir, "TakPtt.WinTak.g.resources"));
foreach (var f in Directory.GetFiles(dir, "*.png"))
    w.AddResource("assets/" + Path.GetFileName(f).ToLowerInvariant(), new MemoryStream(File.ReadAllBytes(f)), true);
w.Generate();
