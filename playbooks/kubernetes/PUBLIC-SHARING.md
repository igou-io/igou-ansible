# Temporary public sharing

`public-share.yaml` supports `create`, `extend`, `remove`, `list-active`, and
`list-available` through one AAP survey. `public-share-cleanup.yaml` removes expired managed resources;
the inventory schedule runs it hourly. Every created Service, route, grant,
and NetworkPolicy carries the same share ID, owner, and UTC expiry annotation.
No application pods, namespaces, Secrets, or persistent data are cleanup targets.

Start with `list-available` (the default action). It prints the approved backend
names, Services, supported protocols/backend ports, public port ranges, and
copyable Create inputs for each supported protocol. It also returns the listing
in job artifacts. This reads the inventory catalog without querying either
cluster or resolving cluster credentials. Example TCP/UDP ports are candidates;
Create checks availability when you submit the request.

Use `list-active` to show your current shares, endpoints, and expiration dates
in job output and artifacts. An optional share ID narrows the listing. Shares
remain listed until removed, including those awaiting hourly expiry cleanup.
The former `list` action is replaced by `list-active`.

For example, `list-available` supplies these inputs for the TCP backend:

```yaml
public_share_action: create
public_share_backend: tcp
public_share_protocols:
  - TCP
public_share_tcp_port: 27010
public_share_expires_at: ''
```

Optional `public_share_subdomain: demo` selects `demo.sharing.igou.io` for all
of the share's HTTPS/TCP/UDP endpoints. Blank input generates
`ps-<job-id>.sharing.igou.io`. Use 1–63 lowercase letters, digits, or internal
hyphens. The `ps-` prefix is reserved; names held by existing shares are rejected even
when the share is expired but not yet removed. Extend and Remove still use
the generated share ID; they cannot change the hostname. Apply the matching
VPS admission policy, managed AAP survey, and Gateway certificate covering
`*.sharing.igou.io` before enabling new names. Existing root-domain shares
retain their addresses and remain extendable and removable.

Create uses the approved `public_share_backends` inventory catalog and an AAP
job ID (`ps-<job-id>`). Select HTTP, TCP, and/or UDP. HTTP gets
`https://ps-<job-id>.sharing.igou.io`; TCP uses a chosen port from 27010–27017 and UDP
from 27018–27025. Backend ports come from the catalog. Empty expiry defaults
to two hours; an explicit date uses `YYYY-MM-DDTHH:MM:SSZ`. The default
maximum lease is 72 hours from each create/extend request, with eight total
shares and three per owner. Inventory configures these limits. Existing partial
and expired shares count until removed; extension renews a lease and is not an
absolute lifetime cap. Cleanup remains hourly.

Extend updates only expiration annotations with UID/version tests. Remove and
cleanup re-read each object and delete with UID/version preconditions. An
update racing cleanup causes a conflict rather than deleting the updated object.
Partial creation remains stamped for cleanup; partial extension is reported
and can be retried. Cleanup attempts other objects before reporting failures.
Expiry means removal on the next successful hourly run, without session-cutoff
or control-plane-outage guarantees.

Install the paired OpenShift public-services component and VPS public-sharing
RBAC/listener configuration first. Configure wildcard DNS and the shared
Tailscale tags: `tag:vps-public-service` for VPS egress proxies and
`tag:ocp-public-service` for OpenShift endpoints. One grant permits all TCP
and UDP ports from the VPS tag to the OpenShift tag. Deploy the application
in OpenShift public-services.
The catalog is configured in inventory. `igou-ansible/examples/public-share-backends.yaml` has three
entries matching `igou-openshift/test-workloads/public-services/`: `http`
selects nginx on TCP 8080, `tcp` selects the separate hostname server on TCP
9000, and `udp` selects the UDP echo server on UDP 9000. Install the workload
and its Tailscale endpoint before approving the entry in inventory. Each entry
declares only its own protocol. The public examples use `example.ts.net`;
replace that suffix in private inventory with your tailnet suffix. Adding an
entry requires no new tailnet tags or grants. Services and NetworkPolicies
constrain the backend ports.

The three static VPS examples in `igou-kubernetes/examples/public-services/`
show the same forwarding objects grouped by protocol. AAP creates temporary
objects with its own share ID and expiration. The job returns the public
hostname/port; use curl for HTTP, a TCP connection to receive the TCP server's
hostname, or `echo hello` to receive a UDP reply. Publishing an absent or
unapproved backend fails.

Before enabling the AAP jobs, store base64-encoded kubeconfig dictionaries in
1Password item `vps-public-sharing` in `lab_aap`, fields `vps_kubeconfig` and
`ocp_kubeconfig`. Use only the staged `aap-public-share` VPS identity and
`aap-public-share-reader` OpenShift identity, with the correct cluster CA and
private API server. Use bounded TokenRequest credentials and rotate them before
expiry. Do not use the Argo/ESO cluster-admin token. Managed AAP configuration
resolves the full inventory lookups into the
`public-sharing-kubeconfigs` credential. The sharing templates attach only this
credential and reuse the existing `igou-awx-ee`; they receive no Connect
token. AAP writes the two secret kubeconfigs to per-job temporary credential
files, exposed by `PUBLIC_SHARE_VPS_KUBECONFIG` and
`PUBLIC_SHARE_OCP_KUBECONFIG`. Playbooks read these files without logging them.
Sync credential types and credentials before templates.
For an authorized local run, supply those same environment variables with
paths to the dedicated scoped kubeconfigs.

AAP callers need execute permission on `vps_public_share`; keep the separate
`vps_public_share_cleanup` template restricted to platform operators. The
manual template disables simultaneous runs and arbitrary extra variables.
