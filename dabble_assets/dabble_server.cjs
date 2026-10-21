"use strict";
/* dabble 0.5 Node.js backend. No npm dependencies. */
const http = require("node:http");
const fs = require("node:fs/promises");
const path = require("node:path");
const {URL} = require("node:url");
const routes = [];
const types = {".html":"text/html; charset=utf-8", ".js":"text/javascript; charset=utf-8",
  ".css":"text/css; charset=utf-8", ".json":"application/json; charset=utf-8",
  ".svg":"image/svg+xml", ".png":"image/png", ".jpg":"image/jpeg",
  ".jpeg":"image/jpeg", ".ico":"image/x-icon", ".webp":"image/webp"};

function truthy(v) {
  if (v == null || v === false || v === 0 || v === "" || Number.isNaN(v)) return false;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object") return Object.keys(v).length > 0;
  return true;
}
function* range(...args) {
  const [start, end, step] = args.length === 1 ? [0,args[0],1]
    : args.length === 2 ? [args[0],args[1],1] : args;
  if (!Number.isInteger(start) || !Number.isInteger(end) || !Number.isInteger(step) || step === 0)
    throw Error("range requires integer limits and nonzero step");
  for (let i=start; step>0 ? i<end : i>end; i+=step) yield i;
}
function route(method, pattern, handler) {
  if (!/^(GET|POST|PUT|PATCH|DELETE)$/.test(method) || typeof pattern !== "string" ||
      !pattern.startsWith("/api/") || typeof handler !== "function") {
    throw Error('route(method, "/api/...", handler) requires an HTTP method and a function');
  }
  const segments = pattern.split("/").slice(1);
  const params = segments.filter(x => x.startsWith(":"));
  if (params.some(x => !/^:[A-Za-z_][A-Za-z0-9_]*$/.test(x))) throw Error("invalid route parameter");
  routes.push({method, pattern, segments, handler});
}
function matchingRoute(method, pathname) {
  const parts = pathname.split("/").slice(1);
  for (const candidate of routes) {
    if (candidate.method !== method || parts.length !== candidate.segments.length) continue;
    const params = {};
    if (candidate.segments.every((seg, i) => {
      if (seg.startsWith(":")) { params[seg.slice(1)] = parts[i]; return true; }
      return seg === parts[i];
    })) return {handler:candidate.handler,params};
  }
  return null;
}
async function readJson(req) {
  const chunks = []; let bytes = 0;
  for await (const chunk of req) {
    bytes += chunk.length;
    if (bytes > 1024 * 1024) { const err = Error("body exceeds 1 MiB"); err.status=413; throw err; }
    chunks.push(chunk);
  }
  if (!bytes) return null;
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { const err=Error("invalid JSON request body"); err.status=400; throw err; }
}
function json(res,status,body) {
  const payload=JSON.stringify(body === undefined ? null : body);
  res.writeHead(status,{"Content-Type":"application/json; charset=utf-8","Cache-Control":"no-store"});
  res.end(payload);
}
function safeAsset(root, urlPath) {
  let name;
  try { name = decodeURIComponent(urlPath); } catch { return null; }
  if (name.includes("\\") || name.includes("\0")) return null;
  if (name === "/") name = "/index.html";
  const parts=name.split("/").filter(Boolean);
  if (parts.some(p => p === ".." || p.startsWith(".") || p === "dabble_packages" ||
                 p.endsWith(".dab") || p.endsWith(".py") || p.endsWith(".cjs") || p === "dabble.json" || p === "dabble.lock")) return null;
  const resolved=path.resolve(root,...parts);
  return resolved.startsWith(root + path.sep) ? resolved : null;
}
async function listen({root,port=8000}) {
  root=path.resolve(root);
  const server=http.createServer(async(req,res)=>{
    const url=new URL(req.url, "http://localhost");
    const found=matchingRoute(req.method, url.pathname);
    if (found) {
      try {
        const body=await readJson(req);
        const result=await found.handler({method:req.method,path:url.pathname,
          query:Object.fromEntries(url.searchParams),params:found.params,body,headers:req.headers});
        json(res,200,result);
      } catch (err) {
        const status=err.status || 500;
        if (status===500) console.error("dabble backend error:",err);
        json(res,status,{error:status===500 ? "Internal server error" : err.message});
      }
      return;
    }
    if (url.pathname.startsWith("/api/")) { json(res,404,{error:"Not found"}); return; }
    if (req.method!=="GET" && req.method!=="HEAD") { json(res,405,{error:"Method not allowed"}); return; }
    let filename=safeAsset(root,url.pathname);
    if (!filename) { json(res,403,{error:"Forbidden"}); return; }
    try {
      const actual = await fs.realpath(filename);
      if (!actual.startsWith(root + path.sep)) { json(res,403,{error:"Forbidden"}); return; }
      let content=await fs.readFile(actual);
      if (url.pathname !== "/" && !(path.extname(filename) in types)) { json(res,404,{error:"Not found"}); return; }
      res.writeHead(200,{"Content-Type":types[path.extname(filename)] || "application/octet-stream", "Cache-Control":"no-cache"});
      res.end(req.method === "HEAD" ? undefined : content);
    } catch(err) {
      if (err.code === "ENOENT" && (req.headers.accept || "").includes("text/html") && !path.extname(url.pathname)) {
        try { const html=await fs.readFile(path.join(root,"index.html")); res.writeHead(200,{"Content-Type":types[".html"]}); res.end(html); }
        catch { json(res,404,{error:"Not found"}); }
      } else { json(res,404,{error:"Not found"}); }
    }
  });
  server.listen(port,"127.0.0.1",()=>console.log(`dabble full-stack: http://127.0.0.1:${server.address().port}/`));
  return server;
}
module.exports={route,listen,truthy,range,len:x=>x.length,str:String,print:console.log};
