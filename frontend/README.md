# RouteBench frontend

Requires Node.js 20.19+ or 22.12+ and the repository Python environment installed at `../.venv`.

```sh
npm ci
npm run dev
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

The development server runs at `http://127.0.0.1:5173` and proxies `/api` to `http://127.0.0.1:8765`. Set `ROUTEBENCH_API_TARGET` to override that backend address. Production assets are written to `dist/` and served by the Python application.

Playwright starts a dedicated mock backend on port 8766 with temporary storage, and a Vite server pointing to it. It asserts mock mode before scheduling evaluations; no model is called. An existing mock backend on that port may be reused. The tests cover readiness, live SSE updates, grading evidence, diffs, normalized events, routing, incomplete cost, incompatible history, exports, and mobile layout. Integration screenshots are saved under the repository `.implementation/` directory.

All benchmark values come from the API. Synthetic fixture values are confined to tests.
