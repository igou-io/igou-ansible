# Modern Molecule Organization Guide

This guide shows how to organize molecule testing contexts using modern practices and native molecule features, without custom Python code.

## Directory Structure

```
molecule/
├── README.md
├── default
│   ├── converge.yml
│   ├── create.yml
│   ├── destroy.yml
│   ├── molecule.yml
│   └── verify.yml
├── kubernetes-bootstrap-kind
│   ├── converge.yml
│   ├── molecule.yml
│   └── verify.yml
├── kubernetes-create-serviceaccount-kind
│   ├── converge.yml
│   ├── molecule.yml
│   └── verify.yml
├── shared
│   ├── base.yml
│   ├── playbooks
│   │   ├── docker-create.yml
│   │   ├── docker-destroy.yml
│   │   ├── kind-create.yml
│   │   ├── kind-destroy.yml
│   │   ├── podman-create.yml
│   │   └── podman-destroy.yml
│   └── templates
│       ├── docker.yml
│       ├── kubernetes.yml
│       └── matrix.yml
...
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
    molecule test -s system-update

# Kubernetes testing with custom kubeconfig
KUBECONFIG=/path/to/config \
K8S_AUTH_VERIFY_SSL=true \
    molecule test -s kubernetes-bootstrap
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
```

### 4. Shared Resources

- Keep common playbooks in `shared/playbooks/`
- Use templates in `shared/templates/`
- Document shared variables in `shared/base.yml`

## Matrix Testing

Matrix tests are leveraged by Github Actions runners

### Debugging

```bash
# Verbose output
MOLECULE_VERBOSITY=2 molecule test -s scenario-name

# Debug specific step
molecule create -s scenario-name
molecule converge -s scenario-name --debug
```
