Test only: send pre-built OpenMANET talk-group RTP packets from an Android phone over adb, on its
Wi-Fi (toybox nc cannot pick the multicast interface). Each file in the folder is one RTP packet;
make them with `tools/omtalk.py`'s format (Opus, PT 111).

    javac --release 8 TgSend.java && $ANDROID_HOME/build-tools/<version>/d8 --min-api 26 --output . TgSend.class
    adb push classes.dex /data/local/tmp/tgsend.dex
    adb shell 'CLASSPATH=/data/local/tmp/tgsend.dex app_process / TgSend /data/local/tmp/pk 1'
