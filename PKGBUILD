# Builds the package from this source checkout:  makepkg -si
# (aur/PKGBUILD is the release version that downloads a tagged tarball.)
pkgname=btwctl
pkgver=0.9.2
pkgrel=1
pkgdesc="Control the Creative BT-W5 and BT-W6 Bluetooth audio dongles (aptX mode, HFP, pairing, headset switching)"
arch=('any')
url="https://github.com/doodersrage/btwctl"
license=('MIT')
depends=('python' 'python-pyusb')
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
checkdepends=('python-pytest')
optdepends=('python-pyqt6: settings window and tray icon'
            'pyside6: settings window and tray icon, alternative to PyQt6'
            'libnotify: desktop notifications (btwctl monitor --notify)')
install=btwctl.install
# btwctl was called btw5-switch before 0.7
replaces=('btw5-switch')
conflicts=('btw5-switch')
provides=('btw5-switch')

build() {
  cd "$startdir"
  rm -rf "$srcdir/dist"   # no stale wheels from earlier versions
  /usr/bin/python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

check() {
  cd "$startdir"
  QT_QPA_PLATFORM=offscreen /usr/bin/python -m pytest -q -p no:cacheprovider
}

package() {
  /usr/bin/python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl
  "$startdir/install-files.sh" "$pkgdir"
}
