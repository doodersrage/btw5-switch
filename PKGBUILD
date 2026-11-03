# Maintainer: your name <you@example.com>
pkgname=btw5-switch
pkgver=0.4
pkgrel=1
pkgdesc="Control the Creative BT-W5 Bluetooth audio dongle (aptX mode, HFP, pairing, headset switching)"
arch=('any')
url="https://github.com/doodersrage/btw5-switch"
license=('unknown')
depends=('python' 'python-pyusb')
install=btw5-switch.install
source=('btw5-switch.py' '70-btw5-switch.rules')
sha256sums=('SKIP' 'SKIP')

package() {
  install -Dm755 "$srcdir/btw5-switch.py" "$pkgdir/usr/bin/btw5-switch"
  install -Dm644 "$srcdir/70-btw5-switch.rules" "$pkgdir/usr/lib/udev/rules.d/70-btw5-switch.rules"
}
