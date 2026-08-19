#!/usr/bin/env node
/**
 * Runs a Python command with the project's virtualenv interpreter when one exists,
 * falling back to whatever `python` is on PATH.
 *
 * Keeps `npm test` / `npm run dev` working whether or not the venv is activated, on
 * Windows and POSIX alike.
 *
 *   node scripts/py.mjs -m pytest backend
 *   node scripts/py.mjs --cwd backend -m scripts.seed
 *
 * `--cwd <dir>` must come first when used; everything after it is passed to Python
 * untouched. Scripts that import `app.*` need it, because the backend directory is
 * what has to be on `sys.path`.
 */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const candidates = [
  join(repoRoot, '.venv', 'Scripts', 'python.exe'),
  join(repoRoot, '.venv', 'bin', 'python'),
];
const interpreter = candidates.find((candidate) => existsSync(candidate)) ?? 'python';

const args = process.argv.slice(2);
let cwd = repoRoot;
if (args[0] === '--cwd') {
  cwd = resolve(repoRoot, args[1] ?? '.');
  args.splice(0, 2);
}

const child = spawn(interpreter, args, { stdio: 'inherit', cwd });

child.on('error', (error) => {
  process.stderr.write(
    `Could not run Python (${interpreter}): ${error.message}\n` +
      'Create the environment first:\n' +
      '  python -m venv .venv\n' +
      '  .venv/Scripts/python -m pip install -r backend/requirements-dev.txt\n',
  );
  process.exit(1);
});

child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 0);
});
