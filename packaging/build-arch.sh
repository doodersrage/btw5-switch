#!/bin/sh
# Build the Arch package in a clean archlinux container (CI):  packaging/build-arch.sh [outdir]
# makepkg refuses to run as root, so this builds as an unprivileged user.
set -e
src=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-$src/dist}
pacman -Syu --noconfirm --needed base-devel git python python-pyusb python-build python-installer \
    python-setuptools python-wheel python-pytest python-pyqt6
id builder >/dev/null 2>&1 || useradd -m builder
work=$(mktemp -d)
cp -r "$src/." "$work/"
rm -rf "$work/.git" "$work"/*.pkg.tar.zst "$work/pkg" "$work/src"
chown -R builder "$work"
su builder -c "cd '$work' && makepkg -f --noconfirm"
mkdir -p "$out"
cp "$work"/btw5-switch-*-any.pkg.tar.zst "$out/"
