.PHONY: lint yamllint syntax-check

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
