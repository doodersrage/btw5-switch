# Builds the package from this source checkout:  makepkg -si
# (aur/PKGBUILD is the release version that downloads a tagged tarball.)
pkgname=btw5-switch
pkgver=0.5
pkgrel=1
pkgdesc="Control the Creative BT-W5 Bluetooth audio dongle (aptX mode, HFP, pairing, headset switching)"
arch=('any')
url="https://github.com/doodersrage/btw5-switch"
license=('LicenseRef-unknown')
depends=('python' 'python-pyusb')
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
checkdepends=('python-pytest')
optdepends=('python-pyqt6: tray icon (btw5-switch tray)'
            'pyside6: tray icon, alternative to PyQt6'
            'libnotify: desktop notifications (btw5-switch monitor --notify)')
install=btw5-switch.install

build() {
  cd "$startdir"
  /usr/bin/python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

check() {
  cd "$startdir"
  /usr/bin/python -m pytest -q -p no:cacheprovider
}

package() {
  /usr/bin/python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl
  "$startdir/install-files.sh" "$pkgdir"
}
