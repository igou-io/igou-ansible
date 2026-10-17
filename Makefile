AAP_NAV_CFG := playbooks/aap/ansible-navigator.yml

.PHONY: lint yamllint syntax-check ee _check-inv aap-configure aap-sync-credentials aap-sync-templates aap-bootstrap-connect ao-export

lint:
	ansible-lint --profile=production

yamllint:
	yamllint .

syntax-check:
	@failed=0; \
	for playbook in $$(find playbooks \( -name '*.yml' -o -name '*.yaml' \) \
	    ! -name 'orchestrator-workflow.yml' \
	    ! -name '*-orchestrator-workflow.yml' | sort); do \
		echo "Checking $${playbook}..."; \
		set --; \
		if [ "$${playbook}" = playbooks/devenv/restore.yml ]; then \
			set -- -e ansible_limit=localhost; \
		fi; \
		if ! ansible-playbook --syntax-check "$${playbook}" "$$@"; then \
			failed=1; \
		fi; \
	done; \
	exit "$${failed}"

ee:
	cd execution-environments/igou-awx-ee && \
		ansible-builder build -v 3 --tag igou-awx-ee:latest

pac-sim:
	tkn pac resolve -f .tekton/igou-aap-ee-rhel9-push.yml | oc create -n ci-igou-ansible -f -

_check-inv:
	@test -n "$(ANSIBLE_INVENTORY)" || { \
	  echo "ANSIBLE_INVENTORY not set (export it pointing at igou-inventory/inventory.yaml)"; \
	  exit 1; \
	}

aap-configure: _check-inv ## Apply all AAP objects via infra.aap_configuration.dispatch
	ANSIBLE_NAVIGATOR_CONFIG=$(AAP_NAV_CFG) \
	  ansible-navigator run playbooks/aap/configure-aap.yml

aap-sync-credentials: _check-inv ## Sync only AAP credentials
	ANSIBLE_NAVIGATOR_CONFIG=$(AAP_NAV_CFG) \
	  ansible-navigator run playbooks/aap/configure-aap-credentials.yml

aap-sync-templates: _check-inv ## Sync only AAP job templates / projects / workflows / schedules
	ANSIBLE_NAVIGATOR_CONFIG=$(AAP_NAV_CFG) \
	  ansible-navigator run playbooks/aap/configure-aap-templates.yml

aap-bootstrap-connect: _check-inv ## Seed the Onepassword Connect credential (needs OP_SERVICE_ACCOUNT_TOKEN)
	ANSIBLE_NAVIGATOR_CONFIG=$(AAP_NAV_CFG) \
	  ansible-navigator run playbooks/aap/bootstrap-connect-credential.yml

ao-export: ## Export a published Automation Orchestrator workflow version to YAML (WF=<workflow-id> VER=<version>)
	@WF="$(WF)" VER="$(VER)" ./hack/ao-export.sh

# Local KubeVirt tests. Functional execution is deliberately outside Actions.
export MOLECULE_GLOB := molecule/*/molecule.yml
MOLECULE_SCENARIO ?= default
MOLECULE_SCENARIOS ?= default linux-node-exporter linux-maintenance
MOLECULE_WORKERS ?= 3
.PHONY: molecule-test molecule-test-all molecule-test-batch molecule-converge molecule-verify molecule-destroy molecule-matrix
molecule-test:
	molecule test -s $(MOLECULE_SCENARIO)
molecule-test-all:
	molecule test --all
molecule-test-batch:
	@test -n "$(MOLECULE_SCENARIOS)" || { echo "Set MOLECULE_SCENARIOS to the scenarios to run"; exit 1; }
	@case "$(MOLECULE_WORKERS)" in ''|*[!0-9]*) echo "MOLECULE_WORKERS must be a positive integer" >&2; exit 1 ;; esac; test "$(MOLECULE_WORKERS)" -gt 0
	MOLECULE_DEPENDENCY_ENABLED=true molecule dependency -s $(firstword $(MOLECULE_SCENARIOS))
	printf '%s\n' $(MOLECULE_SCENARIOS) | xargs -r -n 1 -P $(MOLECULE_WORKERS) env MOLECULE_DEPENDENCY_ENABLED=false molecule test -s
molecule-converge:
	molecule converge -s $(MOLECULE_SCENARIO)
molecule-verify:
	molecule verify -s $(MOLECULE_SCENARIO)
molecule-destroy:
	molecule destroy -s $(MOLECULE_SCENARIO)
molecule-matrix:
	molecule matrix -s $(MOLECULE_SCENARIO) test
