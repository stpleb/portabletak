#!/bin/bash
# Start the PortableTAK server on this Mac (Apple Silicon) from this drive.
exec "$(cd "$(dirname "$0")" && pwd)/.portabletak/mac/portabletak-mac-run" start
