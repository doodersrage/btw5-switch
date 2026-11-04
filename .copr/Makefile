# Fedora COPR "make srpm" build method (SCM source, this repository):
# COPR runs `make -f .copr/Makefile srpm outdir=...` and builds the RPM from the result.
srpm:
	dnf -y install rpm-build tar git
	packaging/build-rpm.sh --srpm $(outdir)
