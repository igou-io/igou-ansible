# alloy

Install [Grafana Alloy](https://grafana.com/docs/alloy/latest/) from the
Grafana package repositories and ship the host's systemd journal to the
central log-gateway (igou-openshift#382 phase 4).

Streams carry `job="systemd-journal"` plus `host` and `unit` from the
journal metadata; `alloy_extra_labels` adds static labels.

| Variable | Default | Description |
|---|---|---|
| `alloy_enabled` | `true` | Enable and start the service |
| `alloy_loki_url` | `""` | **Required.** Loki push endpoint, e.g. `http://syslog.igou.systems:3500/loki/api/v1/push` |
| `alloy_extra_labels` | `{}` | Static labels merged into `loki.write` `external_labels` |
| `alloy_journal_max_age` | `"12h"` | Journal replay window on first start |
| `alloy_config_path` | `/etc/alloy/config.alloy` | Rendered config location |

Used by `playbooks/logging/converge.yaml` on the `linux_logging`
inventory group.
