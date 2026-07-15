# Molecule testing

These tests are biased toward exercising real homelab scenarios without
reprovisioning expensive infrastructure. There is a high probability this
contains some antipatterns for professional settings — the goal is to test
things I rarely do in "production" and that are at high risk of drifting (e.g.
bootstrapping a GitOps/secrets controller onto a fresh cluster after several
major Kubernetes releases have shipped since the last time I did it).

The concepts used to build/organize `molecule.yml` here carry over to
role/collection testing.

## Scenario naming

Scenarios follow a **`<type>-<subject>[-<qualifier>]`** scheme (lowercase,
kebab-case between segments; the `subject` mirrors the content's own name and
keeps its internal underscores):

| Segment | Rule |
| --- | --- |
| `type` | `playbook-` (a playbook under `playbooks/…`), `role-` (a role under `roles/…`), or `logic-` (a localhost-only logic test, no instances). |
| `subject` | The content's own identifier. For a nested playbook domain, include the domain first — e.g. `playbook-windows-join_domain`. |
| `qualifier` | Optional. Disambiguates multiple scenarios for the same subject, and describes the **test** — `e2e`, `smoke`, `absent`, `upgrade`. |

**The backend never goes in the scenario name.** Podman vs. qemu vs. kubevirt is
selected at runtime via `mp_backend` / `PROVISIONER` and matrixed in CI. Each
scenario's `molecule.yml` sets `scenario.name` to match its directory name.

## Provisioning

Instance lifecycle is delegated to the
[`david_igou.molecule_provisioners`](https://galaxy.ansible.com/david_igou/molecule_provisioners)
collection rather than per-scenario create/destroy plumbing. Inventories stay
per-scenario (they describe which instances that scenario tests): a `molecule`
group with `mp.<backend>` host blocks and `mp_backend`/`mp_defaults` in
`group_vars`.

## Directory structure

```
molecule/
├── README.md
├── _windows_common/                 # non-scenario support (no molecule.yml)
│   ├── playbooks/
│   │   └── windows-sysprep-secrets.yml   # imported by every playbook-windows-* create/destroy
│   └── templates/
│       └── windows-unattend.xml.j2       # rendered per-host into a KubeVirt sysprep Secret
├── default/                         # scaffold stub
├── logic-kubevirt-vm-snapshot/      # localhost-only logic test
├── playbook-devenv-bootstrap/
├── playbook-grafana-kiosk/
├── playbook-windows-build_golden_image/
├── playbook-windows-<use_case>/     # nine Windows playbook scenarios
└── role-ghapp-e2e/
```

Leading-underscore directories (e.g. `_windows_common/`) hold reusable plumbing
imported by scenarios; Molecule only treats a directory as a scenario when it
contains a `molecule.yml`, so these are never discovered as scenarios.

## Environment variable templating

Molecule natively supports environment variable substitution in `molecule.yml`,
used here so one scenario covers many shapes (distro, privilege, backend):

```yaml
platforms:
  - name: "${MOLECULE_PLATFORM_NAME:-instance}"
    image: "${MOLECULE_DISTRO_IMAGE:-quay.io/ansible/community-ansible-dev-tools:latest}"
    privileged: ${MOLECULE_PRIVILEGED:-true}
```

## Usage

```bash
# Run one scenario
molecule test -s <scenario-name>

# Iterate without tearing down instances
molecule converge -s <scenario-name>
molecule verify   -s <scenario-name>
molecule destroy  -s <scenario-name>

# Verbose / debug
MOLECULE_VERBOSITY=2 molecule test -s <scenario-name>
```
