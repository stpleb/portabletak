#!/bin/bash
# Builds the plugin jar without a TAKX install, compiling against stubs/ (the few API signatures this
# plugin uses, copied from the TAKX 5.8 Javadoc). stubs/ never goes in the jar. With TAKX installed use
# ./gradlew install instead, which compiles against the real API.
#   tools/build-offline.sh [work dir]     JAVAC and JAR pick the JDK tools; needs JDK 17+
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
work=${1:-$here/build/offline}
JAVAC=${JAVAC:-javac}; JAR=${JAR:-jar}
mkdir -p "$work/lib" "$work/api" "$work/stubs" "$work/classes"
get() { f="$2/$(basename "$1").jar"; [ -f "$f" ] || curl -sSfL -o "$f" "https://repo1.maven.org/maven2/$1.jar"; }
get io/github/jaredmdobson/concentus/1.0.2/concentus-1.0.2 "$work/lib"
get org/bouncycastle/bcprov-jdk18on/1.80/bcprov-jdk18on-1.80 "$work/lib"
get org/bouncycastle/bcpkix-jdk18on/1.80/bcpkix-jdk18on-1.80 "$work/lib"
get org/bouncycastle/bcutil-jdk18on/1.80/bcutil-jdk18on-1.80 "$work/lib"
get jakarta/inject/jakarta.inject-api/2.0.1/jakarta.inject-api-2.0.1 "$work/api"
get jakarta/enterprise/jakarta.enterprise.cdi-api/4.0.1/jakarta.enterprise.cdi-api-4.0.1 "$work/api"
"$JAVAC" --release 17 -nowarn -d "$work/stubs" -cp "$work/api/*" $(find "$here/stubs/src" -name '*.java')
rm -rf "$work/classes"; mkdir -p "$work/classes/lib"
"$JAVAC" --release 17 -Xlint:all,-processing,-serial -d "$work/classes" -cp "$work/stubs:$work/api/*:$work/lib/*" $(find "$here/src/main/java" -name '*.java')
cp "$here"/src/main/resources/* "$work/classes/"
cp "$work"/lib/*.jar "$work/classes/lib/"
"$JAR" cf "$work/TakPtt-TAKX-0.1.0.jar" -C "$work/classes" .
ls -la "$work/TakPtt-TAKX-0.1.0.jar"
