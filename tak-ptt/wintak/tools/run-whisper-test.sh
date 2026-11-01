#!/bin/bash
# Real speech through Whisper on a Mac (say makes the speech): plugin audio path, Opus at the plugin's settings,
# the helper program and the model the plugin ships. Needs the .NET 8 SDK and model/ggml-base.en.bin (build-wpk.sh gets it).
#   tools/run-whisper-test.sh [work dir]     DOTNET picks the dotnet to use
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
work=${1:-$(mktemp -d)}
DOTNET=${DOTNET:-dotnet}
export DOTNET_CLI_TELEMETRY_OPTOUT=1
mkdir -p "$work"
say -o "$work/speech.aiff" "Bravo team this is Titan. Contact at grid seven four two. Moving to the north gate. Over."
afconvert -f WAVE -d LEI16@48000 -c 1 "$work/speech.aiff" "$work/speech.wav"
python3 -I - "$work/speech.wav" "$work/speech.raw" <<'PY'
import sys, wave
w = wave.open(sys.argv[1])
open(sys.argv[2], 'wb').write(w.readframes(w.getnframes()))
PY
"$DOTNET" build -c Release -v quiet --nologo -f net8.0 -o "$work/helper" "$here/TakPtt.Whisper/TakPtt.Whisper.csproj"
"$DOTNET" build -c Release -v quiet --nologo -o "$work/bin" "$here/TakPtt.E2E/TakPtt.E2E.csproj"
PATH="$(dirname "$DOTNET"):$PATH" "$DOTNET" "$work/bin/TakPtt.E2E.dll" whisper "$work/helper/TakPtt.Whisper.dll" "$here/model/ggml-base.en.bin" "$work/speech.raw" "Bravo Titan north gate over"
