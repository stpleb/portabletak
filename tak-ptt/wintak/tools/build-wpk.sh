#!/bin/bash
# Builds the plugin and its Whisper helper and packs them as a .wpk (a zip: manifest.xml, icon.png, plugin/x64/<id>/*), the same
# layout as the plugins that ship with WinTAK. Runs on any computer with the .NET 8 SDK.
#   tools/build-wpk.sh WINTAK_DLL_FOLDER [work dir]      DOTNET picks the dotnet to use
# WINTAK_DLL_FOLDER is a WinTAK install, or the files unpacked from its .msi.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
dlls=$1
work=${2:-$(mktemp -d)}
DOTNET=${DOTNET:-dotnet}
export DOTNET_CLI_TELEMETRY_OPTOUT=1
id=TakPtt
version=1.0.1.0
sdk=5.8.0.146            # also in Plugin/AssemblyInfo.cs

rm -rf "$work/bin" "$work/pack" "$work/whisper"
"$DOTNET" build -c Release -v quiet --nologo -p:WinTakDir="$dlls" -o "$work/bin" "$here/Plugin/TakPtt.WinTak.csproj"
to="$work/pack/plugin/x64/$id"
mkdir -p "$to" "$here/dist"
# Only what the plugin uses: the rest of the build output is NuGet's and could clash with WinTAK's own.
for f in TakPtt.WinTak Concentus NAudio.Core NAudio.WinMM Microsoft.Win32.Registry; do cp "$work/bin/$f.dll" "$to/"; done
# Whisper: the helper in a folder of its own (see TakPtt.Whisper), Windows x64 files only, and the model.
model="$here/model/ggml-base.en.bin"
[ -f "$model" ] || curl -sSfL --create-dirs -o "$model" https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
echo "137c40403d78fd54d454da0f9bd998f78703390c  $model" | shasum -c --quiet
"$DOTNET" build -c Release -v quiet --nologo -f net48 -o "$work/whisper" "$here/TakPtt.Whisper/TakPtt.Whisper.csproj"
mkdir -p "$to/whisper/runtimes"
cp "$work"/whisper/*.dll "$work/whisper/TakPtt.Whisper.exe" "$work/whisper/TakPtt.Whisper.exe.config" "$to/whisper/"
cp -R "$work/whisper/runtimes/win-x64" "$to/whisper/runtimes/"
cp "$model" "$to/whisper/"
cp "$here/Plugin/Assets/takptt_64.png" "$work/pack/icon.png"
printf '\xef\xbb\xbf' > "$work/pack/manifest.xml"
cat >> "$work/pack/manifest.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<wintakPluginPackage>
  <metadata>
    <id>$id</id>
    <version>$version</version>
    <sdkVersion>$sdk</sdkVersion>
    <takVariants>All</takVariants>
    <description>Push to talk over your TAK server's voice server. Signs in with your TAK certificate, so there are no extra accounts. Talks with TAK PTT on ATAK and other Mumble users.</description>
    <dependencies>
    </dependencies>
  </metadata>
</wintakPluginPackage>
XML
out="$here/dist/$id-$version-$sdk.wpk"
rm -f "$out"
(cd "$work/pack" && zip -q -X -D -r "$out" icon.png manifest.xml plugin)
unzip -l "$out"
