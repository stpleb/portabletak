#!/bin/bash
# Copy the current PortableTAK sources into the GitHub repo folder and push.
#   ./sync-repo.sh "commit message"
set -e
W="$(cd "$(dirname "$0")" && pwd)"; O="$W/../outputs"; R=/Users/STPLEB/Documents/portabletak
X=(--exclude=__pycache__ --exclude=.DS_Store --exclude='*.before-*' --exclude='*.pyc' --exclude=.run --exclude=out)
rsync -a --delete "${X[@]}" --exclude='vendor/mediamtx/mediamtx' "$W/portabletak-provision/" "$R/provision/"
rsync -a --delete "${X[@]}" --exclude='payload/usr/local/portabletak/share/tailscale/tailscale*' --exclude='*.tar.gz' \
    --exclude='payload/usr/local/portabletak/share/mediamtx/mediamtx' "$W/host-installer-20260929/" "$R/node-package/"
rsync -a --delete "${X[@]}" "$W/wsl-host/" "$R/windows-host/"
rsync -a --delete "${X[@]}" "$W/mac-vm/" "$R/mac-vm/"
rsync -a --delete "${X[@]}" "$W/mac-setup/" "$R/mac-setup/"
rsync -a --delete "${X[@]}" "$W/win-setup/" "$R/windows-setup/"
rsync -a --delete "${X[@]}" --exclude='*.tar.gz' --exclude='*.tar.gz.sha256' --exclude=vm.qcow2 --exclude=vm.sha256 \
    --exclude=vmlinuz --exclude=initrd.img --exclude='*.msi' --exclude='.portabletak/node/' "$W/start-here/" "$R/start-here/"
rsync -a --delete "${X[@]}" "$W/guides/" "$R/guides/"
rsync -a --delete "${X[@]}" "$W/node-loader/" "$R/node-loader/"
rsync -a --delete "${X[@]}" --exclude=ots --exclude=wheels --exclude='*.deb' --exclude=e2e/A --exclude=e2e/B \
    --exclude=e2e/payload --exclude=e2e/ptapi --exclude='e2e/*.pem' --exclude='e2e/invite.out*' --exclude=testimg \
    --exclude='takserver/build.log' --exclude=secrets "$W/federation/" "$R/federation/"
cp "$O"/*.md "$R/docs/"
cp "$W/sync-repo.sh" "$R/docs/sync-repo.sh"
cp "$W/build-release.sh" "$R/docs/build-release.sh"
cd "$R"
git add -A
if git diff --cached --quiet; then echo "nothing to commit"; exit 0; fi
git -c user.name="Stephen LeBlanc" -c user.email=stpleb@gmail.com commit -q -m "${1:-Update PortableTAK sources}

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -q origin main
git log --oneline | head -1
