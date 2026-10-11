// Local-only operator bridge. Do not deploy this proxy.
export default {
  fetch(request, env) {
    const host = new URL(request.url).hostname;
    if (!['localhost', '127.0.0.1', '[::1]'].includes(host)) return new Response('Forbidden', { status: 403 });
    if (request.headers.has('Origin')) return new Response('Forbidden', { status: 403 });
    return env.DATASETS.fetch(request);
  }
};
