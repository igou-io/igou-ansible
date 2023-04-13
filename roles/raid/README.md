# RAID Role

This Ansible role is designed to configure RAID arrays on Linux systems. It supports creating RAID arrays of levels 1, 6, or 10. It can conditionally wipe filesystems, metadata, and preexisting partitions based on the `wipe` variable.

## Requirements

- Ansible 2.9 or higher
- The target system must have `mdadm` and `parted` installed.

## Role Variables

The following variables are available for configuring the role:

| Variable                 | Default | Description                                                                                   |
|--------------------------|---------|-----------------------------------------------------------------------------------------------|
| `raid_devices`           | `[]`    | List of devices to include in the RAID array                                                  |
| `raid_device_partitions` | `[]`    | List of device partitions to use in the RAID array                                            |
| `raid_level`             | `10`    | RAID level (1, 6, or 10)                                                                     |
| `wipe`                   | `false` | Whether to wipe filesystems, metadata, and preexisting partitions, and rebuild the array      |

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
    raid_level: 10
    wipe: true
```
