import { WorkerEntrypoint } from 'cloudflare:workers';
import { handle } from './service.mjs';

// Never attach this default entrypoint to a public proxy. It reads private data.
export default { fetch: (request, env) => handle(request, env) };

// The public valuation app receives only this restricted entrypoint.
export class Catalog extends WorkerEntrypoint {
  fetch(request) { return handle(request, this.env, true); }
}
