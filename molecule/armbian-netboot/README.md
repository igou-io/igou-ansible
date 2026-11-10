# Armbian lab workflow integration

This scenario imports the real `playbooks/armbian/` build/publication, staging,
boot-mode, reprovision, cycle and full-reimage entry points. Five disposable
KubeVirt guests provide a builder, NFS/HTTP server, RouterOS CHR and two virtual
boards on Casval. Generated inventory and the existing power-transport hook
keep the run separate from production hosts.

The fixtures and assertions live in
[`../shared/armbian_netboot/`](../shared/armbian_netboot/README.md), shared with
[`armbian-netboot-collection`](../armbian-netboot-collection/README.md).
That guide covers credentials, dependencies, the optional collection-source
override, uncached builds and cleanup. No test assets live in the collection.

From the repository root, with the test virtual environment activated and a
suitable OCP credential:

```bash
molecule test -s armbian-netboot
```

The default path repeats cached build/publication and verifies the actual root
filesystem, TFTP delivery, disk integrity and bootstrap access. Full reimage
must leave the automation account working with sudo, password authentication
disabled and root locked. Kexec provides the virtual power transport; Rockchip
U-Boot, SPI and physical PoE remain hardware acceptance.

Lint and syntax checks resolve local fixtures with `ARMBIAN_COLLECTION_SOURCE`
unset. On failure, preserve the state and clean up this run with:

```bash
molecule destroy -s armbian-netboot
```
