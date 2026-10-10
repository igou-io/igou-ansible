// Run in the pinned Renovate image to exercise its actual managers and updater.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const root = process.env.RENOVATE_PACKAGE_ROOT ?? '/usr/local/renovate/dist';
const load = (file) => import(pathToFileURL(path.join(root, file)));
const { init } = await load('logger/index.js');
await init();
const { extractPackageFile: extractRegex } = await load('modules/manager/custom/regex/index.js');
const { extractPackageFile: extractGalaxy, defaultConfig: galaxyDefaults } = await load('modules/manager/ansible-galaxy/index.js');
const { applyPackageRules } = await load('util/package-rules/index.js');
const { matchRegexOrGlob } = await load('util/string-match.js');
const { parseSingleYaml } = await load('util/yaml.js');
const { doAutoReplace } = await load('workers/repository/update/branch/auto-replace.js');
const { GlobalConfig } = await load('config/global.js');
const temporaryRepo = await fs.mkdtemp(path.join(os.tmpdir(), 'ansible-renovate-'));
GlobalConfig.set({ localDir: temporaryRepo, platform: 'local' });
process.on('exit', () => rmSync(temporaryRepo, { recursive: true, force: true }));

const config = JSON.parse(await fs.readFile('renovate.json', 'utf8'));
const read = (file) => fs.readFile(file, 'utf8');
const matchesFile = (patterns, file) => patterns.some((pattern) => matchRegexOrGlob(file, pattern));
const extract = (content, file) => config.customManagers.flatMap((manager) => {
  if (!matchesFile(manager.managerFilePatterns, file)) return [];
  const result = extractRegex(content, file, manager);
  return (result?.deps ?? []).map((dep, depIndex) => ({ ...manager, ...result, ...dep, depIndex, packageFile: file }));
});
const route = (dep, manager = 'custom.regex', updateType = 'minor') => applyPackageRules({
  ...dep, packageName: dep.packageName ?? dep.depName, manager, updateType,
  automerge: true, groupName: 'inherited group', packageRules: config.packageRules,
});

const requirementsFile = 'requirements-molecule.yml';
const requirementsContent = await read(requirementsFile);
const requirements = parseSingleYaml(requirementsContent);
assert.ok(matchesFile([...galaxyDefaults.managerFilePatterns, ...config['ansible-galaxy'].managerFilePatterns], requirementsFile));
const galaxy = (await extractGalaxy(requirementsContent, requirementsFile)).deps;
assert.equal(galaxy.length, requirements.roles.length + requirements.collections.length);
const provisioner = galaxy.find((dep) => dep.depName === 'david_igou.molecule_provisioners');
assert.equal(provisioner.currentValue, requirements.collections.find((dep) => dep.name === provisioner.depName).version);
assert.equal((await route({ ...provisioner, packageFile: requirementsFile }, 'ansible-galaxy')).automerge, false);
assert.equal((await route({ ...provisioner, packageFile: requirementsFile }, 'ansible-galaxy')).groupName, 'molecule requirements');

for (const file of ['requirements.yml', requirementsFile]) {
  const content = await read(file);
  const parsed = (await extractGalaxy(content, file)).deps;
  for (const dep of parsed.filter((item) => item.datasource === 'github-tags')) {
    const policy = await route({ ...dep, packageFile: file }, 'ansible-galaxy');
    assert.equal(policy.packageName, dep.packageName.replace('https://github.com/', '').replace(/\.git$/, ''));
    if (['main', 'fix-user-update'].includes(dep.currentValue) || /^[a-f0-9]{40}$/.test(dep.currentValue)) {
      assert.equal(policy.enabled, false, `${file}: explicit branch/SHA must stay manual`);
    } else {
      assert.notEqual(policy.enabled, false, `${file}: release tags must remain enabled`);
      const updated = await doAutoReplace({ ...dep, manager: 'ansible-galaxy', packageFile: file,
        depIndex: parsed.indexOf(dep), newValue: '99.99.99' }, content, false);
      assert.ok(updated.includes(dep.packageName), 'Ansible git source URL must survive lookup normalization');
      const updatedDep = (await extractGalaxy(updated, file)).deps[parsed.indexOf(dep)];
      assert.equal(updatedDep.currentValue, '99.99.99');
    }
  }
}

const tools = [
  ['.github/workflows/ee-build.yml', 'ansible-builder', 'pypi'],
  ['roles/fedora_desktop/defaults/main.yml', 'node', 'node-version'],
  ['playbooks/windows/deploy_ohmgraphite.yaml', 'nickbabcock/OhmGraphite', 'github-releases'],
  ['playbooks/upsmonitor/setup-exporter.yml', 'DRuggeri/nut_exporter', 'github-releases'],
  ['playbooks/grafana-kiosk/setup-kiosk.yaml', 'grafana/grafana-kiosk', 'github-releases'],
];
for (const [file, name, datasource] of tools) {
  const content = await read(file);
  const found = extract(content, file).filter((dep) => dep.depName === name);
  assert.equal(found.length, 1, `${file}: extract ${name} exactly once`);
  const dep = found[0];
  assert.equal(dep.datasource, datasource);
  const policy = await route(dep);
  assert.equal(policy.automerge, false, `${name}: compatibility/checksum review required`);
  assert.equal(policy.groupName, null, `${name}: no unrelated tool grouping`);
  const updated = await doAutoReplace({ ...dep, manager: 'regex', newValue: '99.99.99' }, content, false);
  assert.equal(updated, content.replace(dep.replaceString, dep.replaceString.replace(dep.currentValue, '99.99.99')));
  parseSingleYaml(updated);
}

const kioskFile = 'playbooks/grafana-kiosk/setup-kiosk.yaml';
const kioskContent = await read(kioskFile);
const kiosk = extract(kioskContent, kioskFile).find((dep) => dep.depName === 'grafana/grafana-kiosk');
const checksums = parseSingleYaml(kioskContent)[0].vars._kiosk_gk_checksums[kiosk.currentValue];
assert.ok(checksums, 'A grafana-kiosk update must include reviewed checksums for its new version');
for (const arch of ['amd64', 'arm64', 'armv7']) assert.match(checksums[arch] ?? '', /^[a-f0-9]{64}$/, `Missing grafana-kiosk ${arch} checksum`);

const images = [
  ['ansible-navigator.yml', 'quay.io/igou/igou-awx-ee', 1],
  ['playbooks/aap/ansible-navigator.yml', 'quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9', 1],
  ['playbooks/openshift/bootstrap_gitops.yaml', 'quay.io/raffaelespazzoli/raffa-envsub', 1],
  ['playbooks/openshift_virtualization/build_windows_golden.yml', 'registry.redhat.io/container-native-virtualization/virtio-win-rhel9', 1],
  ['molecule/shared/inventory/group_vars/all/main.yml', 'quay.io/containerdisks/debian', 1],
  ['molecule/grafana-kiosk/inventory/hosts.yml', 'quay.io/containerdisks/debian', 1],
  ['molecule/grafana-kiosk/inventory/group_vars/molecule/main.yml', 'quay.io/containerdisks/debian', 1],
];
const newDigest = `sha256:${'a'.repeat(64)}`;
for (const [file, name, count] of images) {
  const content = await read(file);
  const found = extract(content, file).filter((dep) => dep.depName === name);
  assert.equal(found.length, count, `${file}: image must be extracted`);
  for (const dep of found) {
    assert.equal(dep.datasource, 'docker');
    assert.equal((await route(dep)).automerge, false);
    const updated = await doAutoReplace({ ...dep, manager: 'regex', newValue: dep.currentValue, newDigest, isPinDigest: !dep.currentDigest }, content, false);
    const ref = `${name}:${dep.currentValue}@${newDigest}`;
    assert.equal(updated, content.replace(dep.replaceString, ref), `${file}: preserve embedded YAML and indentation`);
    parseSingleYaml(updated);
  }
}

const imageManager = config.customManagers.find((manager) => manager.matchStringsStrategy === 'recursive');
for (const quote of ['', '"', "'"]) {
  const content = `---\nspec:\n  image: ${quote}registry.example:5000/team/image:1.2.3${quote} # preserve me\n`;
  const result = extractRegex(content, 'ansible-navigator.yml', imageManager);
  assert.equal(result.deps.length, 1, 'No spurious image field or registry port dependencies');
  const dep = result.deps[0];
  const updated = await doAutoReplace({ ...imageManager, ...result, ...dep, depIndex: 0,
    manager: 'regex', packageFile: 'ansible-navigator.yml', newValue: '1.2.4', newDigest, isPinDigest: true }, content, false);
  assert.equal(updated, content.replace('registry.example:5000/team/image:1.2.3', `registry.example:5000/team/image:1.2.4@${newDigest}`));
}
assert.equal(extractRegex('image: "{{ image }}"\nimage: $(params.BUILDER_IMAGE)\n', 'ansible-navigator.yml', imageManager), null);
assert.equal(extract('image: quay.io/team/test:1.2.3\n', 'unrelated.yml').length, 0);

const bootstrapFile = 'playbooks/devenv/bootstrap.yml';
const bootstrapContent = await read(bootstrapFile);
const bootstrap = extract(bootstrapContent, bootstrapFile);
const git = bootstrap.find((dep) => dep.depName === 'igou-io/igou-devenv');
const image = bootstrap.find((dep) => dep.depName === 'ghcr.io/igou-io/igou-devenv');
assert.ok(git && image, 'Both bootstrap release pins must extract');
assert.equal(git.currentValue.replace(/^v/, ''), image.currentValue, 'Bootstrap git and image releases must agree');
for (const dep of [git, image]) {
  const policy = await route(dep);
  assert.equal(policy.groupName, 'devenv bootstrap release');
  assert.equal(policy.automerge, false);
  const newValue = dep === git ? 'v2099.01.01-1' : '2099.01.01-1';
  const updated = await doAutoReplace({ ...dep, manager: 'regex', newValue }, bootstrapContent, false);
  assert.equal(extract(updated, bootstrapFile).find((item) => item.depName === dep.depName).currentValue, newValue);
  parseSingleYaml(updated);
}
console.log(`Renovate coverage passed: ${galaxy.length} Molecule requirements, Git collection lookup/replacement, ${tools.length} tool pins, ${images.length} image locations, quoted/embedded image updates, kiosk checksums and paired bootstrap releases.`);
