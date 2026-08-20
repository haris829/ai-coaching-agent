#!/usr/bin/env node
/**
 * Deploy the Courses Quiz Agent to Railway, end to end, and verify the result.
 *
 *     node scripts/deploy-railway.mjs                 # create everything and deploy
 *     node scripts/deploy-railway.mjs --skip-create   # redeploy into the existing project
 *
 * Requires `railway login` to have been completed (or `RAILWAY_API_TOKEN` in the environment).
 * Everything else — project, PostgreSQL, the application service, variables, secrets, domain — this
 * does.
 *
 * WHY A SCRIPT RATHER THAN A LIST OF COMMANDS IN A DOCUMENT
 * --------------------------------------------------------
 * Three things have to be right and each fails in a way that points somewhere else:
 *
 *   * **The application needs its own service.** `railway add --database postgres` creates the
 *     database *and links it as the active service*, so a bare `railway up` afterwards deploys the
 *     application **into the Postgres service** — replacing its image, inheriting its healthcheck,
 *     and leaving one service that is neither a database nor an app. That happened on the first
 *     real run of this script. Every command below therefore names `--service` explicitly rather
 *     than trusting whatever is linked.
 *   * **PostgreSQL must exist before the app first boots**, because the app migrates on start. Boot
 *     it first and you get a healthy container and a 500 from every endpoint that touches a table.
 *   * **The guard tokens must be set before the first deploy**, because `Settings` refuses to start
 *     a non-development environment without them. That refusal is deliberate — but on Railway it
 *     looks like a crash loop with the reason buried in the logs.
 *
 * Generating the secrets here also means they are never typed, never pasted, and never committed.
 */

import { execFileSync, execSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';

const PROJECT_NAME = process.env.RAILWAY_PROJECT_NAME ?? 'courses-quiz-agent';
/** The application service. Named, because every command has to be able to point at it. */
const APP_SERVICE = process.env.RAILWAY_APP_SERVICE ?? 'quiz-agent';
/** Railway's own name for the database service it creates, used in the variable reference below. */
const DB_SERVICE = process.env.RAILWAY_DB_SERVICE ?? 'Postgres';

/** A token nobody has to invent, remember, or accidentally commit. */
function secret(label) {
  return `${label}_${randomBytes(24).toString('base64url')}`;
}

function run(command, args, { capture = false, allowFail = false } = {}) {
  console.log(`\n$ ${command} ${args.join(' ')}`);
  try {
    const output = execFileSync(command, args, {
      encoding: 'utf8',
      stdio: capture ? ['inherit', 'pipe', 'pipe'] : 'inherit',
      shell: process.platform === 'win32',
      maxBuffer: 32 * 1024 * 1024,
    });
    if (capture && output) process.stdout.write(output);
    return output ?? '';
  } catch (error) {
    if (allowFail) {
      console.log(`  (non-fatal) failed: ${String(error.message).split('\n')[0]}`);
      return '';
    }
    throw error;
  }
}

const railway = (args, options) => run('railway', args, options);

// ---------------------------------------------------------------------------
// 0. Preconditions
// ---------------------------------------------------------------------------
console.log('COURSES QUIZ AGENT — RAILWAY DEPLOYMENT');
console.log('='.repeat(60));

try {
  const who = execSync('railway whoami', { encoding: 'utf8', shell: true });
  console.log(`authenticated as: ${who.trim()}`);
} catch {
  console.error(
    '\nNot signed in to Railway.\n\n' +
      '  Run `railway login` and approve it in the browser *promptly* — the device code expires in\n' +
      '  a few minutes and an approval after that writes no credentials. Or set RAILWAY_API_TOKEN,\n' +
      '  which has no such window.\n',
  );
  process.exit(2);
}

const skipCreate = process.argv.includes('--skip-create');

// ---------------------------------------------------------------------------
// 1. Project, database, then the application service — in that order
// ---------------------------------------------------------------------------
if (!skipCreate) {
  railway(['init', '--name', PROJECT_NAME]);

  // PostgreSQL first: the application migrates on start, so it needs a database to migrate into
  // before its first boot rather than after.
  railway(['add', '--database', 'postgres']);

  // The application gets a service of its own. Without this, `railway up` would deploy into the
  // service `add --database` just linked, which is the Postgres one.
  railway(['add', '--service', APP_SERVICE]);
}

// ---------------------------------------------------------------------------
// 2. Variables, on the application service
// ---------------------------------------------------------------------------
const variables = {
  ENVIRONMENT: 'production',
  // A reference rather than a copy: Railway substitutes the database service's own URL, so a
  // rotated credential does not leave a stale literal behind.
  DATABASE_URL: `\${{${DB_SERVICE}.DATABASE_URL}}`,
  FRONTEND_DIST: '/app/frontend/dist',

  // The two guards the application refuses to start without.
  ADMIN_API_TOKEN: secret('admin'),
  SYSTEM_API_TOKEN: secret('system'),

  // A review deployment: it must hand a reviewer a way in, and have something to review.
  AUTO_SEED: 'true',
  DEMO_IDENTITIES: 'true',
  SEED_ADMIN_TOKEN: secret('seedadmin'),
  SEED_LEARNER_TOKEN: secret('seedlearner'),
  SEED_LEARNER2_TOKEN: secret('seedlearner2'),
  SEED_ASSESSOR_TOKEN: secret('seedassessor'),

  LOG_LEVEL: 'INFO',
};

const setArgs = [];
for (const [key, value] of Object.entries(variables)) {
  setArgs.push('--set', `${key}=${value}`);
}
railway(['variables', '--service', APP_SERVICE, ...setArgs]);

// ---------------------------------------------------------------------------
// 3. Build and deploy — explicitly into the application service
// ---------------------------------------------------------------------------
railway(['up', '--ci', '--service', APP_SERVICE]);

// ---------------------------------------------------------------------------
// 4. A public URL, for the application service
// ---------------------------------------------------------------------------
let domainOutput = railway(['domain', '--service', APP_SERVICE], {
  capture: true,
  allowFail: true,
});
let match = /([a-z0-9-]+\.up\.railway\.app)/i.exec(domainOutput);
if (!match) {
  // `domain` with no argument generates one; if it had already been generated the first call
  // reports it instead. Either way a second read is harmless.
  domainOutput = railway(['domain', '--service', APP_SERVICE, '--json'], {
    capture: true,
    allowFail: true,
  });
  match = /([a-z0-9-]+\.up\.railway\.app)/i.exec(domainOutput);
}

const url = match ? `https://${match[1]}` : null;

console.log('\n' + '='.repeat(60));
if (!url) {
  console.log(
    'Deployed, but no public domain was reported. Generate one and verify:\n' +
      `  railway domain --service ${APP_SERVICE}\n` +
      '  npm run verify:deployment -- --base-url <url> --yes',
  );
  process.exit(0);
}

console.log(`public URL : ${url}`);
console.log(`API        : ${url}/api`);
console.log(`API docs   : ${url}/api/docs`);
console.log(`health     : ${url}/api/health`);

// ---------------------------------------------------------------------------
// 5. Verify — because a completed build is not a working application
// ---------------------------------------------------------------------------
console.log('\nWaiting for the deployment to answer its health check…');
const deadline = Date.now() + 420_000;
let healthy = false;
while (Date.now() < deadline) {
  try {
    const response = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(10_000) });
    if (response.ok) {
      const body = await response.json();
      console.log(
        `  healthy: database=${body.database} environment=${body.environment} ` +
          `modules=${body.modules?.length}`,
      );
      healthy = true;
      break;
    }
  } catch {
    // Not up yet. A cold start includes the migration, so this can take a minute or two.
  }
  await new Promise((resolve) => setTimeout(resolve, 5_000));
}

if (!healthy) {
  console.error(
    '\nThe deployment did not become healthy in time.\n' +
      `  railway logs --service ${APP_SERVICE}\n` +
      '  The migration runs before the server binds; a failure there is fatal and logged.\n',
  );
  process.exit(1);
}

console.log('\nRunning the six review journeys against the deployment…');
run('node', [
  'scripts/py.mjs',
  '--cwd',
  'backend',
  '-m',
  'scripts.verify_deployment',
  '--base-url',
  url,
  '--yes',
]);

console.log('\n' + '='.repeat(60));
console.log('Deployment verified. The reviewer\'s credentials are listed by:');
console.log(`  curl -s ${url}/api/session`);
console.log(`\nThe generated guard tokens are in Railway's variables for ${APP_SERVICE}.`);
