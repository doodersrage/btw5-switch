#!/bin/sh
# Build an RPM from this source tree:  packaging/build-rpm.sh [outdir]   (needs rpm-build)
set -e
src=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-$src}
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$src/btwctl/__init__.py")
top=$(mktemp -d)
trap 'rm -rf "$top"' EXIT
mkdir -p "$top/SOURCES" "$top/staging/btwctl-$version"
# the same files a release tarball has (no capture/screenshot, no .git)
(cd "$src" && tar --exclude=.git --exclude=bt-w5.pcapng --exclude=btw5-wireshark.png \
    --exclude=__pycache__ --exclude=pkg --exclude=src --exclude='*.pkg.tar.zst' --exclude=build \
    --exclude='*.egg-info' -cf - .) | tar -C "$top/staging/btwctl-$version" -xf -
tar -C "$top/staging" -czf "$top/SOURCES/btwctl-$version.tar.gz" "btwctl-$version"
sed "s/^Version:.*/Version:        $version/" "$src/packaging/btwctl.spec" > "$top/btwctl.spec"
rpmbuild -bb --define "_topdir $top" "$top/btwctl.spec"
mkdir -p "$out"
cp "$top"/RPMS/noarch/*.rpm "$out/"
