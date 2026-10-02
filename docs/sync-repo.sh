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
    --exclude=vmlinuz --exclude=initrd.img --exclude='*.msi' "$W/start-here/" "$R/start-here/"
rsync -a --delete "${X[@]}" "$W/guides/" "$R/guides/"
cp "$O"/*.md "$R/docs/"
cp "$W/sync-repo.sh" "$R/docs/sync-repo.sh"
cd "$R"
git add -A
if git diff --cached --quiet; then echo "nothing to commit"; exit 0; fi
git -c user.name="Stephen LeBlanc" -c user.email=stpleb@gmail.com commit -q -m "${1:-Update PortableTAK sources}

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -q origin main
git log --oneline | head -1
