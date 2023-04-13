# RAID Role

This Ansible role is designed to configure RAID arrays on Linux systems. It supports creating RAID arrays of levels 1, 6, or 10, and adding the array to the `/etc/fstab` file with specified mount options.

## Requirements

- Ansible 2.9 or higher
- The target system must have `mdadm` and `parted` installed.

## Role Variables

The following variables are available for configuring the role:

| Variable                 | Default | Description                                                                                   |
|--------------------------|---------|-----------------------------------------------------------------------------------------------|
| `raid_devices`           | `[]`    | List of devices to include in the RAID array                                                  |
| `raid_device_partitions` | `[]`    | List of device partitions to use in the RAID array                                            |
| `mount_point`            | `/mnt/raid` | Mount point for the RAID array                                                              |
| `raid_level`             | `10`    | RAID level (1, 6, or 10)                                                                     |
| `add_to_fstab`           | `true`  | Whether to add the RAID array to the `/etc/fstab` file (with options `defaults,nofail,discard`) |
| `rebuild_raid_array`     | `false` | Whether to zero the devices and rebuild the array from scratch                                |

## Dependencies

None.

## Example Playbook

Here's an example playbook that uses the `raid` role:

```yaml
---
- name: Configure RAID array
  hosts: all
  become: yes
  roles:
    - raid
  vars:
    raid_devices:
      - /dev/sda
      - /dev/sdb
      - /dev/sdc
      - /dev/sdd
    raid_device_partitions:
      - /dev/sda1
      - /dev/sdb1
      - /dev/sdc1
      - /dev/sdd1
    mount_point: /mnt/raid
    raid_level: 10
    add_to_fstab: true
```

## Example Inventory

Here's an example inventory that sets host variables for the RAID configuration:

```yaml
all:
  children:
    storage:
      hosts:
        raid.mynode.com:
          sysctl_overwrite:
            vm.mmap_rnd_bits: 16
          raid_devices:
            - /dev/sda
            - /dev/sdb
            - /dev/sdc
            - /dev/sdd
          raid_device_partitions:
            - /dev/sda1
            - /dev/sdb1
            - /dev/sdc1
            - /dev/sdd1
          mount_point: /mnt/raid
          raid_level: 10
          add_to_fstab: true
```
