import { createServer } from './api/server';
import { buildUC04 } from './composition-root';

/** Development entry point. UC-04 runs standalone with mock adapters. */
const port = Number.parseInt(process.env['PORT'] ?? '4004', 10);
const app = createServer(buildUC04());

app.listen(port, () => {
  // eslint-disable-next-line no-console
  console.log(`UC-04 Course Content Coaching listening on http://localhost:${port}/api/v1/uc04`);
});
