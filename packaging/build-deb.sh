#!/bin/sh
# Build the Debian/Ubuntu package with the debian/ packaging:  packaging/build-deb.sh [outdir]
# Needs: build-essential debhelper dh-python python3-pytest (dpkg-buildpackage runs the tests).
set -e
src=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-$src/dist}
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$src/btwctl/__init__.py")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# build from a clean copy (no .git, no capture/screenshot: like a release tarball)
mkdir "$work/btwctl-$version"
(cd "$src" && tar --exclude=.git --exclude=bt-w5.pcapng --exclude=btw5-wireshark.png \
    --exclude=__pycache__ --exclude=pkg --exclude=src --exclude='*.pkg.tar.zst' --exclude=dist \
    --exclude=build --exclude='*.egg-info' -cf - .) | tar -C "$work/btwctl-$version" -xf -
# the package version follows btwctl/__init__.py
sed -i "1s/^btwctl ([^)]*)/btwctl ($version)/" "$work/btwctl-$version/debian/changelog"
(cd "$work/btwctl-$version" && dpkg-buildpackage -us -uc -b)
mkdir -p "$out"
cp "$work"/btwctl_*_all.deb "$out/"
