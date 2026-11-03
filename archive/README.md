# Archived automation

This directory preserves retired automation and its exclusive templates/files.
It is outside the active `playbooks/` tree and is excluded from Ansible lint,
YAML lint, and Renovate updates. These are historical snapshots, not supported
entry points; recover and adapt them before reuse. Their contents retain their
original paths and assumptions in comments, and are unchanged from retirement.

| Archived path | Retirement reason | Maintained replacement |
| --- | --- | --- |
| `playbooks/hermes/` | The Hermes VM was retired in August 2026; its AAP templates, workflows, and schedules were removed. | Persona/operator deployments in `igou-openshift/applications/hermes-*`. |
| `playbooks/rpi-image/build.yml`, `converge.yml`, and `templates/config.yaml.j2` | The rpi-image-gen pipeline was superseded; [issue #305](https://github.com/igou-io/igou-ansible/issues/305) closed with the release-image replacement. | [`playbooks/rpi-image/build_from_release.yml`](../playbooks/rpi-image/build_from_release.yml), [`flash.yml`](../playbooks/rpi-image/flash.yml), and the Pi netboot entry points remain active. |

The audit and complete usage registers are maintained in
[igou-docs](https://github.com/igou-io/igou-docs/blob/main/ansible-aap/Playbook%20and%20Job%20Template%20Archive%20Audit.md).
