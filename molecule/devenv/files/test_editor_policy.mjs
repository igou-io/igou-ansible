import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, stat, access, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';

const program = resolve(process.env.EDITOR_POLICY_PROGRAM ?? 'playbooks/devenv/files/editor_policy.mjs');
const validator = process.env.EDITOR_POLICY_VALIDATOR;
if (!validator) throw new Error('Set EDITOR_POLICY_VALIDATOR to the pinned Marketplace validator');

async function fixture(t, scenario) {
  const home = await mkdtemp(join(tmpdir(), 'vscode-host-policy-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const config = join(home, 'pins.json');
  await writeFile(config, JSON.stringify({ customizations: { vscode: {
    extensions: ['example.extension@1.0.0'],
  } } }));
  const loader = join(home, 'marketplace-fixture.mjs');
  await writeFile(loader, `
    globalThis.fetch = async (url) => {
      if (url === 'https://fixture.invalid/manifest') return Response.json({
        publisher: 'example', name: 'extension', version: '1.0.0',
        extensionDependencies: ${JSON.stringify(scenario === 'dependency' ? ['other.extension'] : [])},
      });
      if (url !== 'https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery') {
        throw new Error('Unexpected request: ' + url);
      }
      return Response.json({ results: [{ extensions: [{
        publisher: { publisherName: 'example' }, extensionName: 'extension',
        versions: [{ version: '1.0.0', targetPlatform: 'universal',
          lastUpdated: ${JSON.stringify(scenario === 'missing-date' ? null :
            new Date(Date.now() - (scenario === 'young' ? 1 : 20) * 86400000).toISOString())},
          files: [{ assetType: 'Microsoft.VisualStudio.Code.Manifest', source: 'https://fixture.invalid/manifest' }],
        }],
      }] }] });
    };
  `);
  const run = (...args) => spawnSync(process.execPath, [program, '--config', config,
    '--validator', validator, ...args], {
    encoding: 'utf8', env: { ...process.env, HOME: home,
      NODE_OPTIONS: `--import=${pathToFileURL(loader)}` },
  });
  return { home, run };
}

for (const [scenario, reason] of [
  ['young', /less than 10 days old/],
  ['missing-date', /Missing publication date/],
  ['dependency', /Unpinned dependency/],
]) {
  test(`reject ${scenario} without writing host settings`, async t => {
    const { home, run } = await fixture(t, scenario);
    const result = run('--apply');
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, reason);
    await assert.rejects(access(join(home, '.vscode-server')));
    await assert.rejects(access(join(home, '.claude.json')));
  });
}

test('apply an aged pin while preserving unrelated configuration and private file modes', async t => {
  const { home, run } = await fixture(t, 'old');
  const settings = join(home, '.vscode-server/data/User/settings.json');
  await mkdir(join(home, '.vscode-server/data/User'), { recursive: true });
  await writeFile(settings, JSON.stringify({ 'editor.fontSize': 15 }), { mode: 0o600 });
  await writeFile(join(home, '.claude.json'), JSON.stringify({ unrelated: 'preserve' }), { mode: 0o600 });
  const result = run('--apply');
  assert.equal(result.status, 0, result.stderr);
  const policy = JSON.parse(await readFile(settings, 'utf8'));
  assert.equal(policy['editor.fontSize'], 15);
  assert.deepEqual(policy['extensions.allowed'], {
    'example.extension': ['1.0.0'], 'anthropic.claude-code': false,
  });
  assert.equal(policy['extensions.autoUpdate'], false);
  assert.equal(policy['extensions.autoUpdateDelay'], 240);
  assert.equal(policy['extensions.verifySignature'], true);
  assert.equal((await stat(settings)).mode & 0o777, 0o600);
  const claude = JSON.parse(await readFile(join(home, '.claude.json'), 'utf8'));
  assert.deepEqual(claude, { unrelated: 'preserve', autoInstallIdeExtension: false });
  assert.equal((await stat(join(home, '.claude.json'))).mode & 0o777, 0o600);
});

test('validation-only mode leaves host settings untouched', async t => {
  const { home, run } = await fixture(t, 'old');
  const result = run();
  assert.equal(result.status, 0, result.stderr);
  await assert.rejects(access(join(home, '.vscode-server')));
  await assert.rejects(access(join(home, '.claude.json')));
});

test('remove Claude extension artifacts, preserve other extensions, and converge without changes twice', async t => {
  const { home, run } = await fixture(t, 'old');
  const extensions = join(home, '.vscode-server/extensions');
  const claude = join(extensions, 'anthropic.claude-code-2.1.314-linux-x64');
  const retained = join(extensions, 'example.extension-1.0.0');
  await mkdir(claude, { recursive: true });
  await mkdir(retained);
  await writeFile(join(claude, 'extension.js'), 'malware fixture; never execute');
  await writeFile(join(extensions, 'extensions.json'), JSON.stringify([
    { identifier: { id: 'Anthropic.claude-code' }, version: '2.1.314' },
    { identifier: { id: 'example.extension' }, version: '1.0.0' },
  ]));
  const preview = run();
  assert.equal(preview.status, 0, preview.stderr);
  await access(claude);
  const first = run('--apply');
  assert.equal(first.status, 0, first.stderr);
  await assert.rejects(access(claude));
  await access(retained);
  const inventory = JSON.parse(await readFile(join(extensions, 'extensions.json'), 'utf8'));
  assert.deepEqual(inventory, [{ identifier: { id: 'example.extension' }, version: '1.0.0' }]);
  const second = run('--apply');
  assert.equal(second.status, 0, second.stderr);
  assert.match(second.stdout, /Changed files: 0/);
});
