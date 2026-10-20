// Learn more: https://docs.expo.dev/guides/customizing-metro/
const http = require('http');
const { getDefaultConfig } = require('expo/metro-config');

const config = getDefaultConfig(__dirname);

// Dev-only proxy: on web the app calls the API same-origin (`/v1/...`) and the dev server forwards it
// to the UDSE backend, so `uv run udse serve` works unchanged (no CORS). Native builds call the backend
// directly. Override the target with UDSE_API_URL.
const target = new URL(process.env.UDSE_API_URL || 'http://127.0.0.1:8765');
const proxied = (url) => /^\/(v1\/|health(\?|$))/.test(url || '');

const enhance = config.server && config.server.enhanceMiddleware;
config.server = {
  ...config.server,
  enhanceMiddleware: (middleware, server) => {
    const next = enhance ? enhance(middleware, server) : middleware;
    return (req, res, fallthrough) => {
      if (!proxied(req.url)) return next(req, res, fallthrough);
      const upstream = http.request(
        {
          hostname: target.hostname,
          port: target.port || 80,
          method: req.method,
          path: req.url,
          headers: { ...req.headers, host: target.host },
        },
        (up) => {
          res.writeHead(up.statusCode || 502, up.headers);
          up.pipe(res);
        },
      );
      upstream.on('error', (e) => {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ detail: `UDSE backend unreachable at ${target.origin} (${e.code || e.message}). Start it with: uv run udse serve` }));
      });
      req.pipe(upstream);
    };
  },
};

module.exports = config;
