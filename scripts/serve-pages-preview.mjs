import { createServer } from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { extname, resolve, sep } from "node:path";

const root = resolve("dist");
const prefix = "/build123d-flow/";
const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".json": "application/json; charset=utf-8",
  ".wasm": "application/wasm",
  ".data": "application/octet-stream",
  ".py": "text/x-python; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".woff": "font/woff",
  ".woff2": "font/woff2"
};

createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url || "/", "http://localhost").pathname);
  if (pathname !== prefix.slice(0, -1) && !pathname.startsWith(prefix)) {
    response.writeHead(404).end("Not found");
    return;
  }

  const relative = pathname === prefix.slice(0, -1) ? "index.html" : pathname.slice(prefix.length);
  let file = resolve(root, relative);
  if (file !== root && !file.startsWith(root + sep)) {
    response.writeHead(403).end("Forbidden");
    return;
  }

  if (!existsSync(file) || !statSync(file).isFile()) {
    file = resolve(root, "index.html");
  }
  if (!existsSync(file)) {
    response.writeHead(500).end("Production build is missing; run npm run build first");
    return;
  }

  response.writeHead(200, {
    "Content-Type": types[extname(file)] || "application/octet-stream",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff"
  });
  if (request.method === "HEAD") response.end();
  else createReadStream(file).pipe(response);
}).listen(4173, "127.0.0.1", () => {
  process.stdout.write("Pages preview listening at http://127.0.0.1:4173/build123d-flow/\n");
});
