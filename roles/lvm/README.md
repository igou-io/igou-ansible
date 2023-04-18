# Ansible LVM Role

This Ansible role allows you to create and manage LVM configurations with support for multiple volume groups, logical volumes, and thin pools.

## Features

- Idempotent LVM management
- Support for multiple volume groups
- Support for logical volumes, including resizing and removal
- Support for thin pools and thin logical volumes
- Input validation and error handling
- Compatible with Debian and RedHat-based distributions

## Usage

Include the role in your playbook and configure the `lvm_vgs` variable with the desired LVM configuration.

### Example

```yaml
- name: Configure LVM with multiple volume groups, logical volumes, and thin pools
  hosts: all
  become: yes
  roles:
    - lvm
  vars:
    lvm_vgs:
      - name: vg_data
        pv_devices:
          - /dev/md0
        logical_volumes:
          - name: lv_data1
            size: 20G
          - name: lv_data2
            size: 30G
            state: absent
        thin_pools:
          - name: tp_data
            size: 100G
        thin_volumes:
          - name: thin_lv_data
```

