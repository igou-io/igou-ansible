# RAID Role

This Ansible role helps you create and manage RAID arrays on your target hosts. It supports both UUIDs and standard device paths in the `raid_devices` variable. The role is idempotent, ensuring that the RAID array is only created or modified if it does not exist or does not match the desired configuration.

## Role Variables

* `raid_devices`: A list of block devices or UUIDs (using the `UUID=<UUID>` format) to include in the RAID array. (required)
* `raid_name`: The name of the RAID array (e.g., `md0`). (required)
* `raid_level`: The RAID level to use for the array (e.g., `0`, `1`, `5`, `6`, or `10`). (required)
* `wipe`: A boolean that indicates whether to force a rebuild of the RAID array if it already exists. Set to `true` to wipe and rebuild, or `false` (default) to only create the RAID array if it does not exist or does not match the desired configuration.

## Example Playbook

```yaml
---
- name: Configure RAID array on target hosts
  hosts: all
  become: yes
  roles:
    - raid
  vars:
    raid_devices:
      - /dev/sdb
      - /dev/sdc
    raid_name: md0
    raid_level: 10
    wipe: false
```
