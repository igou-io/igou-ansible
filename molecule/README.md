# Modern Molecule Organization Guide

This guide shows how to organize molecule testing contexts using modern practices and native molecule features, without custom Python code.

## Directory Structure

```
molecule/
├── shared/                    # Shared resources
│   ├── templates/            # Template configurations
│   │   ├── podman.yml       # Podman-based testing
│   │   ├── kubernetes.yml   # Kubernetes testing
│   │   └── matrix.yml       # Matrix testing template
│   ├── playbooks/           # Shared playbooks
│   │   ├── podman-create.yml
│   │   ├── podman-destroy.yml
│   │   ├── kind-create.yml
│   │   └── kind-destroy.yml
│   ├── base.yml            # Common base settings
│   └── test-runner.sh      # Helper script
├── Makefile                # Build automation
├── system-update-v2/       # Example scenarios
├── kubernetes-bootstrap-v2/
└── ...
```

## Key Principles

### 1. Environment Variable Templating (Native Support)

Molecule natively supports environment variable substitution in `molecule.yml`:

```yaml
platforms:
  - name: "${MOLECULE_PLATFORM_NAME:-instance}"
    image: "${MOLECULE_DISTRO_IMAGE:-quay.io/ansible/community-ansible-dev-tools:latest}"
    command: "${MOLECULE_PODMAN_COMMAND:-}"
    privileged: ${MOLECULE_PRIVILEGED:-true}
```

### 2. Shared Playbooks for Common Operations

Instead of duplicating create/destroy logic, use shared playbooks:

```yaml
provisioner:
  playbooks:
    create: ../shared/playbooks/podman-create.yml
    destroy: ../shared/playbooks/podman-destroy.yml
    converge: converge.yml
    verify: verify.yml
```

### 3. Template-Based Scenario Creation

Use templates as starting points:

```bash
# Copy template to create new scenario
make copy-template SCENARIO=my-new-test TEMPLATE=podman

# Or using the test runner
./shared/test-runner.sh copy my-new-test podman
```

## Usage Examples

### Basic Testing

```bash
# Test single scenario
make test SCENARIO=system-update MOLECULE_DISTRO=ubuntu22

# Test all scenarios
make test-all

# Matrix testing across distributions
make matrix-test SCENARIO=system-update
```

### Environment Variables for Customization

```bash
# Custom image and settings
MOLECULE_DISTRO_IMAGE="ubuntu:20.04" \
MOLECULE_PRIVILEGED=false \
TEST_ENVIRONMENT=staging \
    molecule test -s system-update

# Kubernetes testing with custom kubeconfig
KUBECONFIG=/path/to/config \
K8S_AUTH_VERIFY_SSL=true \
    molecule test -s kubernetes-bootstrap
```

### Parallel Testing

```bash
# Run multiple scenarios in parallel
make test-all

# Or using the test runner
./shared/test-runner.sh parallel system-update kubernetes-bootstrap default
```

## Template Types

### 1. Podman Template (`shared/templates/podman.yml`)

- Uses containers.podman collection
- Supports multiple distributions
- Environment variable driven
- Suitable for: system testing, application testing

### 2. Kubernetes Template (`shared/templates/kubernetes.yml`)

- Uses Kind for local K8s clusters
- Configurable cluster settings
- Supports custom kubeconfig
- Suitable for: Kubernetes operators, cluster testing

### 3. Matrix Template (`shared/templates/matrix.yml`)

- Environment-driven matrix testing
- Multiple platform support
- Customizable through variables
- Suitable for: multi-distribution testing

## Best Practices

### 1. Avoid Boilerplate

- Use shared playbooks for create/destroy operations
- Leverage environment variables for customization
- Copy templates instead of writing from scratch

### 2. Environment Variables for Flexibility

```yaml
# Good: Configurable through environment
image: "${MOLECULE_DISTRO_IMAGE:-rockylinux:9}"
privileged: ${MOLECULE_PRIVILEGED:-true}

# Bad: Hard-coded values
image: "rockylinux:9"
privileged: true
```

### 3. Consistent Naming

```bash
# Scenario naming
system-update-multiplatform
kubernetes-bootstrap-v2
application-deployment-test

# Environment variables
MOLECULE_DISTRO=ubuntu22
MOLECULE_PLATFORM_NAME=test-instance
TEST_ENVIRONMENT=staging
```

### 4. Shared Resources

- Keep common playbooks in `shared/playbooks/`
- Use templates in `shared/templates/`
- Document shared variables in `shared/base.yml`

## Development Workflow

### 1. Create New Scenario

```bash
# Copy from template
make copy-template SCENARIO=my-app-test TEMPLATE=podman

# Customize the molecule.yml as needed
# Add specific platforms, variables, etc.
```

### 2. Development Testing

```bash
# Create environment without running full test
make dev-create SCENARIO=my-app-test

# Iterate on your playbook
make dev-converge SCENARIO=my-app-test

# Clean up when done
make dev-destroy SCENARIO=my-app-test
```

### 3. CI/CD Integration

```bash
# In CI pipeline
make lint                    # Lint all scenarios
make syntax                  # Check syntax
make test-all               # Run all tests
```

## Matrix Testing

### Environment-Driven Matrix

```bash
# Test multiple distributions
for distro in rockylinux9 ubuntu22 centos9; do
    MOLECULE_DISTRO=$distro molecule test -s system-update
done

# Or use make target
make matrix-test SCENARIO=system-update
```

### Multiple Environment Testing

```bash
# Test different environments
for env in development staging production; do
    TEST_ENVIRONMENT=$env molecule test -s my-scenario
done
```

## Troubleshooting

### Common Issues

1. **Module not found**: Ensure `containers.podman` collection is installed
2. **Permission denied**: Check `MOLECULE_PRIVILEGED` setting
3. **Image not found**: Verify `MOLECULE_DISTRO_IMAGE` value
4. **Kubeconfig issues**: Set `KUBECONFIG` environment variable

### Debugging

```bash
# Verbose output
MOLECULE_VERBOSITY=2 molecule test -s scenario-name

# Debug specific step
molecule create -s scenario-name
molecule converge -s scenario-name --debug
```

## Migration from Custom Code

If you have existing custom Python code:

1. **Replace custom inheritance** with template copying
2. **Use environment variables** instead of config generation
3. **Leverage Makefile** or shell scripts for automation
4. **Use shared playbooks** for common operations

This approach uses only native molecule features and standard tooling, making it more maintainable and easier to understand.
