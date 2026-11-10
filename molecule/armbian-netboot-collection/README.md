# Armbian collection workflow integration

Exercise the collection's published playbooks and roles against the shared
[Armbian netboot fixtures](../shared/armbian_netboot/README.md). All test assets
and lab placement belong to `igou-ansible`.

From the repository root, with the test environment and credentials configured:

```bash
molecule test -s armbian-netboot-collection
```

The scenario covers cached build/publication, shared-image staging and forced
refresh, RouterOS TFTP delivery, raw/xz imaging, rejected targets, provisioning,
bootstrap, data preservation, NFS/SD/local/local-kernel boots and retention.
The shared guide documents the optional `ARMBIAN_COLLECTION_SOURCE` override
for testing collection changes and `LAB_NETBOOT_BUILD=true` for uncached builds.
The [lab-playbook scenario](../armbian-netboot/README.md) adds full-reimage
acceptance using the same fixtures with its own independent lifecycle.

For an interrupted run, preserve state and use:

```bash
molecule destroy -s armbian-netboot-collection
```
