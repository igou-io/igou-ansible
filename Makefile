AAP_NAV_CFG := playbooks/aap/ansible-navigator.yml

.PHONY: lint yamllint syntax-check _check-inv aap-configure aap-sync-credentials aap-sync-templates

lint:
	ansible-lint --profile=production

yamllint:
	yamllint .

syntax-check:
	@failed=0; \
	for playbook in $$(find playbooks -name '*.yml' -o -name '*.yaml' | sort); do \
		echo "Checking $${playbook}..."; \
		if ! ansible-playbook --syntax-check "$${playbook}"; then \
			failed=1; \
		fi; \
	done; \
	exit "$${failed}"

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
