#!/bin/sh
# Start the PortableTAK server on this Linux computer or Raspberry Pi (64-bit) from this drive.
# In a terminal, from this folder:  sudo bash "Start PortableTAK (Linux).sh"
# After the first start, plugging the drive in (or booting) starts it by itself;
# stop it with:  sudo portabletak stop
exec bash "$(cd "$(dirname "$0")" && pwd)/.portabletak/linux/portabletak-linux" start "$@"
