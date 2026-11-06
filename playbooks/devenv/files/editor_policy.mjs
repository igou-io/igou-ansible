#!/usr/bin/env node
// Apply the same age-validated extension pins to host editor installations.
import { readFile, writeFile, mkdir, rename, stat, readdir, rm } from 'node:fs/promises';
import { dirname, resolve, join } from 'node:path';
import { homedir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { parseArgs } from 'node:util';

const { values } = parseArgs({ options: {
  apply: { type: 'boolean', default: false },
  config: { type: 'string' },
  validator: { type: 'string' },
  home: { type: 'string', default: homedir() },
} });
if (!values.config || !values.validator) throw new Error('--config and --validator are required');
const configPath = resolve(values.config);
const config = JSON.parse(await readFile(configPath, 'utf8'));
const pins = config.customizations.vscode.extensions;
if (!pins.length || pins.some(pin => !/^[\w-]+\.[\w-]+@\d+\.\d+\.\d+$/.test(pin))) {
  throw new Error('Host policy requires nonempty, exact extension pins');
}
if (pins.some(pin => pin.toLowerCase().startsWith('anthropic.claude-code@'))) {
  throw new Error('Claude Code is excluded from the host extension policy');
}

// Validate live publication timestamps and every dependency before any write.
const validation = spawnSync(process.execPath, [
  resolve(values.validator), '--config', configPath,
], { stdio: 'inherit' });
if (validation.error) throw validation.error;
if (validation.status !== 0) process.exit(validation.status ?? 1);

const allowed = Object.fromEntries(pins.map(pin => {
  const [id, version] = pin.split('@');
  return [id.toLowerCase(), [version]];
}));
allowed['anthropic.claude-code'] = false;
const policy = {
  'extensions.allowed': allowed,
  'extensions.autoUpdate': false,
  'extensions.autoUpdateDelay': 240,
  'extensions.verifySignature': true,
};
const home = resolve(values.home);
const editors = [
  '.vscode-server/data/Machine/settings.json',
  '.vscode-server/data/User/settings.json',
  '.local/share/code-server/User/settings.json',
  '.config/Code/User/settings.json',
];
const files = editors.map(relative => ({ relative, update: current => ({ ...current, ...policy }) }));
files.push(
  { relative: '.claude.json', update: current => ({ ...current, autoInstallIdeExtension: false }) },
  { relative: '.claude/settings.json', update: current => ({ ...current,
    env: { ...current.env, CLAUDE_CODE_IDE_SKIP_AUTO_INSTALL: '1' } }) },
);
const changes = [];
for (const { relative, update } of files) {
  const file = join(home, relative);
  let current = {};
  let original;
  let mode = 0o600;
  try {
    original = await readFile(file, 'utf8');
    current = JSON.parse(original);
    mode = (await stat(file)).mode & 0o777;
  } catch (error) {
    // JSON parser errors can include the offending text from credential-bearing
    // user settings. Report the path and error type without exposing content.
    if (error.code !== 'ENOENT') throw new Error(`Cannot read settings ${file}: ${error.code ?? error.name}`);
  }
  if (!current || Array.isArray(current) || typeof current !== 'object') {
    throw new Error(`Settings must be a JSON object: ${file}`);
  }
  const content = `${JSON.stringify(update(current), null, 2)}\n`;
  if (content !== original) changes.push({ file, mode, content });
}

// Remove executable Claude extension artifacts while retaining conversation
// history and forensic logs. Existing unrelated extensions are untouched.
const removed = [];
for (const relative of ['.vscode-server/extensions', '.vscode-server/data/CachedExtensionVSIXs',
  '.vscode/extensions', '.local/share/code-server/extensions', '.local/share/code-server/CachedExtensionVSIXs']) {
  const directory = join(home, relative);
  let entries;
  try { entries = await readdir(directory); }
  catch (error) { if (error.code === 'ENOENT') continue; throw error; }
  for (const entry of entries) {
    if (/^anthropic\.claude-code(?:-|$)/i.test(entry)) removed.push(join(directory, entry));
  }
  const file = join(directory, 'extensions.json');
  try {
    const original = await readFile(file, 'utf8');
    const extensions = JSON.parse(original);
    const retained = extensions.filter(extension => extension.identifier.id.toLowerCase() !== 'anthropic.claude-code');
    if (retained.length !== extensions.length) {
      changes.push({ file, mode: (await stat(file)).mode & 0o777,
        content: `${JSON.stringify(retained, null, 2)}\n` });
    }
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
}

if (!values.apply) {
  console.log('Validated host policy; pass --apply to write editor settings.');
} else {
  for (const { file, mode, content } of changes) {
    await mkdir(dirname(file), { recursive: true });
    const temporary = `${file}.${process.pid}.tmp`;
    await writeFile(temporary, content, { mode, flag: 'wx' });
    await rename(temporary, file);
    console.log(`Applied host extension policy: ${file}`);
  }
  for (const file of removed) {
    await rm(file, { recursive: true });
    console.log(`Removed Claude extension artifact: ${file}`);
  }
}
console.log(`Changed files: ${changes.length + removed.length}`);
