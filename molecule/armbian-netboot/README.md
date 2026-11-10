# Armbian lab workflow integration

This companion scenario imports the real `playbooks/armbian/` entry points and
reuses the collection's `lab_netboot` fixtures, assertions and provisioner
lifecycle. It does not copy the five-guest infrastructure or use production
inventory. Kexec replaces physical power control for the x86 board substitutes.

From the repository root, with Casval Ready and a suitable OCP credential:

```bash
export ARMBIAN_COLLECTION_SOURCE=/workspace/ansible-collection-armbian
use ocp
oc whoami --show-server
oc whoami
molecule test -s armbian-netboot
```

Point `ARMBIAN_COLLECTION_SOURCE` at the checkout containing `lab_netboot` when
testing a feature branch. Install this repository's `requirements-molecule.txt`
in a local virtual environment and activate it. The shared fixture also needs
the controller tools documented in its README, including libssh headers when
`ansible-pylibssh` has no wheel for the controller's Python. The
dependency step uses this repository's requirements and then installs the
working collection. Casval placement, the `molecule` namespace and
`lvms-casval` disks are required; this scenario does not implement the TrueNAS
storage profile.

The default run repeats the cached-build and publication workflow. Set
`LAB_NETBOOT_BUILD=true` to request uncached compilation instead.

The production staging, router convergence, NFS-to-local reprovision and full-reimage
playbooks are imported directly. Test inventory supplies generated SSH keys,
run-specific credentials and the existing power-transport hook. Rootfs checks
inspect the actual mounted `/` in the guest. The shared assertions also check
TFTP delivery, disk data, bootstrap authentication and excluded-disk integrity.
Rockchip firmware, SPI and actual PoE remain hardware acceptance checks.

The three playbooks that import optional cross-repo fixtures are excluded from
automatic repository lint discovery. Lint them explicitly with the source
checkout available; Molecule also syntax-checks the composed workflow before
creating guests:

```bash
ARMBIAN_COLLECTION_SOURCE=/workspace/ansible-collection-armbian \
  ansible-lint --offline molecule/armbian-netboot/*.yml
```

On failure, retain the Molecule state and use the same source/profile:

```bash
molecule destroy -s armbian-netboot
```
