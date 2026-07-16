# Windows playbooks

Common Windows automation use cases built on the current (2026) Windows
collection landscape. Each playbook is deliberately small, heavily
commented, and driven by a handful of `windows_*` variables — they are
meant to be read as much as run.

Full design rationale: see the pull request that introduced this
directory (a local copy lives in `docs/superpowers/specs/`, which is
not tracked in git).

## Playbooks

| Playbook | Use case |
|---|---|
| `ping_and_facts.yaml` | Connectivity smoke test + system summary. **Run this first.** |
| `provision_test_machine.yaml` | Flagship: test user + configurable applications + RDP, for agent-driven app testing on OpenShift Virtualization VMs |
| `install_applications.yaml` | Declarative application management with Chocolatey |
| `manage_local_users.yaml` | Local users and groups |
| `install_updates.yaml` | Windows Update with optional reboot-until-clean loop |
| `configure_firewall.yaml` | Firewall profiles + declarative rule list |
| `manage_services.yaml` | Service state and startup mode |
| `create_scheduled_task.yaml` | Scheduled tasks (Windows cron) |
| `deploy_iis_website.yaml` | IIS role + app pool + site + serve-check (Server editions only) |
| `join_domain.yaml` | Active Directory join/leave via microsoft.ad |

All plays target `hosts: "{{ ansible_limit | default('windows') }}"` — pass
`-e ansible_limit=<host-or-group>` or create a `windows` group (below).

## Collections used

Pinned in the repo-root `requirements.yml`
(`ansible-galaxy collection install -r requirements.yml`):

| Collection | Used for | Notes |
|---|---|---|
| `ansible.windows` | core modules (`win_user`, `win_service`, `win_updates`, ...) | the supported core collection |
| `community.windows` | `win_firewall_rule`, `win_scheduled_task` | maintenance mode — no new modules land here |
| `microsoft.ad` | domain join, AD objects | replaces the removed `win_domain*` modules |
| `microsoft.iis` | IIS sites/app pools | replaces the deprecated `community.windows.win_iis_*` |
| `chocolatey.chocolatey` | application packages | bootstraps Chocolatey from the internet on first use |

The `igou-aap-ee-rhel9` execution environment already ships
`ansible.windows`/`community.windows` and the `pywinrm`/`pypsrp` Python
libraries; the other EEs carry the Python libraries too.

Honest caveat: `microsoft.iis` and `microsoft.ad` are NOT among the
collections that `igou-aap-ee-rhel9` bundles. Running
`deploy_iis_website.yaml` or `join_domain.yaml` under AAP therefore needs
those two collections added to the EE first (out of scope here). Local and
molecule runs are unaffected — they install everything from the repo-root
`requirements.yml`, which pins both.

## Connecting to Windows

No `windows` group exists in `igou-inventory` yet. When adding one, put the
connection plumbing in `igou-inventory/group_vars/windows.yml` following the
repo convention (per-group connection vars, per-host `ansible_host`).

WinRM over HTTPS with NTLM — works out of the box for standalone/workgroup
hosts:

```yaml
---
# igou-inventory/group_vars/windows.yml
ansible_connection: winrm
ansible_port: 5986
ansible_winrm_transport: ntlm
# Lab only: Windows generates a self-signed cert for the HTTPS listener
ansible_winrm_server_cert_validation: ignore
ansible_user: Administrator
ansible_password: "{{ lookup('community.general.onepassword', 'windows-administrator', field='password', vault='lab_agents') }}"
```

Alternative — PSRP (PowerShell Remoting over WinRM). Same NTLM / port 5986
/ self-signed-cert-ignore plumbing as `winrm`, just a different connection
plugin backed by `pypsrp` (already in the EEs). This is what the molecule
scenarios use:

```yaml
---
ansible_connection: psrp
ansible_port: 5986
ansible_psrp_auth: ntlm
# Lab only: accept the self-signed HTTPS listener cert
ansible_psrp_cert_validation: ignore
ansible_user: Administrator
ansible_password: "{{ lookup('community.general.onepassword', 'windows-administrator', field='password', vault='lab_agents') }}"
```

Alternative — native SSH (ansible-core ≥ 2.18, needs Win32-OpenSSH in the
guest with PowerShell as the default shell): key auth, no certificate
plumbing:

```yaml
---
ansible_connection: ssh
ansible_shell_type: powershell
ansible_user: Administrator
```

Once hosts are domain-joined, switch `ansible_winrm_transport` to
`kerberos` (required for anything double-hop, e.g. AD management from a
member server).

## Windows VMs on OpenShift Virtualization

Ansible can only take over once the guest has a WinRM/SSH listener and a
known credential. For KubeVirt/OpenShift Virtualization Windows VMs:

1. **VirtIO + guest agent** — attach the `virtio-win` container disk;
   install the drivers and `guest-agent\qemu-ga-x86_64.msi` (without the
   agent, OpenShift can't report the VM's IP or shut it down gracefully).
2. **First-boot bootstrap** — either a **sysprep answer file**
   (`autounattend.xml`/`unattend.xml` in a Secret, mounted via the
   KubeVirt `sysprep` volume) or **cloudbase-init** in the golden image.
   Use it to set the Administrator password and enable the WinRM HTTPS
   listener (or install OpenSSH).
3. **Hand-off** — run `ping_and_facts.yaml` to prove connectivity, then
   `provision_test_machine.yaml` to create the test user and applications
   the agent will exercise.

The `sysprep`/golden-image pipeline itself is out of scope here — see the
design spec for the planned follow-up.

## Running

```bash
# Prove connectivity
ansible-navigator run playbooks/windows/ping_and_facts.yaml \
  -i igou-inventory/inventory.yaml -e ansible_limit=<host>

# Provision an application test machine
ansible-navigator run playbooks/windows/provision_test_machine.yaml \
  -i igou-inventory/inventory.yaml -e ansible_limit=<host> \
  -e windows_test_user_password='<secret>' \
  -e '{"windows_test_apps": [{"name": "firefox"}, {"name": "vlc"}]}'
```

## Conventions

- Variables are prefixed `windows_` and documented in each playbook header.
- Passwords arrive via extra_vars (ideally a
  `community.general.onepassword` lookup) and the tasks that handle them
  set `no_log: true` or rely on the module's own argument-level redaction.
- No `become:` — privilege comes from the connection user; Windows
  `runas` escalation is a separate mechanism you rarely need.
- Reboots are opt-in (`install_updates.yaml`) or module-managed where a
  reboot is inherent to the operation (`join_domain.yaml`).
