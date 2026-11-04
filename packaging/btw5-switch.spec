# Fedora / RPM spec. Build from a source checkout with packaging/build-rpm.sh,
# or use it on COPR (Source0 points at the tagged release).
%global srcname btw5-switch

Name:           btw5-switch
Version:        0.6
Release:        1%{?dist}
Summary:        Control the Creative BT-W5 and BT-W6 Bluetooth audio dongles
License:        MIT
URL:            https://github.com/doodersrage/btw5-switch
Source0:        %{url}/archive/refs/tags/v%{version}/%{srcname}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
Requires:       python3-pyusb
Recommends:     python3-pyqt6
Recommends:     libnotify

%description
Switch aptX Adaptive between High Quality and Low Latency, turn the headset
microphone (HFP) on or off, pair and switch between headsets, and get notified
when a headset connects. Includes a settings window, a tray icon and a command
line tool.

%prep
%autosetup -n %{srcname}-%{version}

%build

%install
mkdir -p %{buildroot}%{python3_sitelib} %{buildroot}%{_bindir}
cp -r btw5 %{buildroot}%{python3_sitelib}/
cat > %{buildroot}%{_bindir}/btw5-switch <<'PY'
#!/usr/bin/python3
from btw5.cli import main
main()
PY
chmod 755 %{buildroot}%{_bindir}/btw5-switch
./install-files.sh %{buildroot}
# rpm puts these itself
rm -rf %{buildroot}%{_datadir}/licenses %{buildroot}%{_docdir}

%post
udevadm control --reload-rules >/dev/null 2>&1 || :
udevadm trigger --subsystem-match=usb --attr-match=idVendor=041e >/dev/null 2>&1 || :

%postun
udevadm control --reload-rules >/dev/null 2>&1 || :

%files
%license LICENSE
%doc README.md contrib/polybar.ini contrib/waybar
%{_bindir}/btw5-switch
%{python3_sitelib}/btw5/
%{_udevrulesdir}/70-btw5-switch.rules
%{_userunitdir}/btw5-switch-monitor.service
%{_datadir}/applications/btw5-switch.desktop
%{_datadir}/applications/btw5-switch-tray.desktop
%{_datadir}/bash-completion/completions/btw5-switch
%{_datadir}/zsh/site-functions/_btw5-switch
%{_datadir}/fish/vendor_completions.d/btw5-switch.fish

%changelog
* Tue Nov 03 2026 Robert McDowell <doodersrage@gmail.com> - 0.6-1
- Settings window, tray icon, per-headset settings, game mode, packages
