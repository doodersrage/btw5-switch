# Publishing btwctl

Every release (push a tag `vX.Y`, see the README's Development section) puts `.deb`, `.rpm` and Arch
packages on the GitHub release page automatically. The channels below give users automatic updates
through their package manager; each needs an account once. CI checks on every push that the packaging
for all of them still builds.

## Fedora: COPR

1. Sign in at <https://copr.fedorainfracloud.org> with a Fedora account (free).
2. *New project* `btwctl`, chroots: the current Fedora releases (e.g. `fedora-*-x86_64`; the package is `noarch`).
3. *Packages → New package → SCM*:
   - Clone URL: `https://github.com/doodersrage/btwctl`
   - Committish: the release tag, e.g. `v0.8` (or leave empty for the latest code)
   - Build SRPM with: **make srpm** (uses `.copr/Makefile`)
4. *Rebuild*. Optionally add a GitHub webhook (COPR → Settings → Integrations) to build every tag.

Users then install with:

```sh
sudo dnf copr enable doodersrage/btwctl
sudo dnf install btwctl
```

## Ubuntu: Launchpad PPA

One-time setup:
1. Create a Launchpad account, upload your GPG key and sign the Ubuntu Code of Conduct.
2. Create a PPA named `btwctl` (<https://launchpad.net/~YOUR-ID/+activate-ppa>).
3. `sudo apt install devscripts debhelper dh-python dput`

For each release and Ubuntu series (Launchpad needs one upload per series, e.g. `jammy`, `noble`):

```sh
git checkout v0.8
dch -v "0.8~noble1" -D noble "New upstream release."   # edits debian/changelog (don't commit this)
debuild -S -sa                                        # signed source package in ../
dput ppa:YOUR-ID/btwctl ../btwctl_0.8~noble1_source.changes
git checkout debian/changelog
```

Users then install with:

```sh
sudo add-apt-repository ppa:YOUR-ID/btwctl
sudo apt install btwctl
```

## Arch: AUR

(New AUR account registration was closed at the time of writing.)

1. Create an AUR account and add your SSH public key.
2. After tagging a release: in `aur/`, set `pkgver`, run `updpkgsums` and `makepkg --printsrcinfo > .SRCINFO`.
3. `git clone ssh://aur@aur.archlinux.org/btwctl.git`, copy `PKGBUILD`, `.SRCINFO` and `btwctl.install`
   into it, commit and push.

The package `replaces` the old `btw5-switch` name, so nothing needs to happen for that.
