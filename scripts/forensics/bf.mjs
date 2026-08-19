import { chromium } from 'playwright';
import fs from 'node:fs';
const A = process.argv.slice(2);
function arg(n,d){const i=A.indexOf('--'+n);return (i!==-1&&A[i+1])?A[i+1]:d;}
const BASE = arg('base','https://d7d88qs4jmch.cloudfront.net').replace(/\/+$/,'');
const PASS = arg('pass','1');
const HARD = A.includes('--hard');
const SOAK = parseInt(arg('soak','0'),10);
const NT = 45000, SETTLE = parseInt(arg('settle','3500'),10);
const PUB = ['/','/signin','/signup'];
const PROT = ['/app/dashboard','/app/strategies','/app/builder','/app/backtest','/app/marketplace','/app/exchange','/app/risk','/app/portfolio','/app/trades','/app/signal-trace','/app/billing','/app/library','/app/notifications','/app/support','/app/profile','/app/security','/app/referrals'];
const ROUTES = PUB.concat(PROT);
const SENS = new Set(['authorization','cookie','set-cookie','apikey','x-api-key','x-supabase-auth']);
function rh(h){const o={};for(const k of Object.keys(h||{})){o[k]=SENS.has(k.toLowerCase())?'[REDACTED]':h[k];}return o;}
function rd(s){if(typeof s!=='string')return s;let r=s.replace(/eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}/g,'[JWT]');r=r.replace(/(access_token|refresh_token|password|apikey|secret)([=:\s\x22]+)([^\s\x22,}]+)/gi,'$1$2[RED]');return r;}
const EXT=/(googletagmanager|google-analytics|clarity\.ms|sentry\.io|sentry-cdn|doubleclick)/i;
function so(u,b){try{return new URL(u).host===new URL(b).host;}catch(e){return false;}}
async function cap(ctx, route) {
  const page = await ctx.newPage();
  const r = { route: route, finalUrl: null, navStatus: null, navError: null, durationMs: null, console: [], pageErrors: [], rejections: [], requests: [], responses: [], failed: [], websockets: [], redirects: [] };
  const st = new Map();
  page.on('console', function(m){ let l={}; try{l=m.location()||{};}catch(e){} r.console.push({type:m.type(),text:rd(m.text()).slice(0,1200),url:l.url||null,line:l.lineNumber}); });
  page.on('pageerror', function(e){ r.pageErrors.push({name:e.name||'Error',message:rd(e.message||String(e)).slice(0,1200),stack:rd(e.stack||'').slice(0,2500)}); });
  page.on('request', function(q){ st.set(q,Date.now()); r.requests.push({method:q.method(),url:rd(q.url()).slice(0,700),rt:q.resourceType(),nav:q.isNavigationRequest(),headers:rh(q.headers())}); });
  page.on('response', function(s){ const q=s.request(); const t=st.get(q); const e={url:rd(s.url()).slice(0,700),status:s.status(),method:q.method(),rt:q.resourceType(),durationMs:t?Date.now()-t:null,xcache:s.headers()['x-cache']||null}; if(s.status()>=300&&s.status()<400){r.redirects.push({from:e.url,to:rd(s.headers()['location']||'').slice(0,700),status:s.status()});} r.responses.push(e); });
  page.on('requestfailed', function(q){ r.failed.push({method:q.method(),url:rd(q.url()).slice(0,700),rt:q.resourceType(),failure:(q.failure()&&q.failure().errorText)||'unknown'}); });
  page.on('websocket', function(w){ const o={url:rd(w.url()).slice(0,400),events:[]}; w.on('close',function(){o.events.push({type:'close'});}); w.on('socketerror',function(x){o.events.push({type:'error',error:rd(String(x)).slice(0,400)});}); r.websockets.push(o); });
  await page.addInitScript(function(){ window.__FR__=[]; window.addEventListener('unhandledrejection', function(e){ let m=''; try{m=(e.reason&&(e.reason.stack||e.reason.message))||String(e.reason);}catch(x){m='unserializable';} window.__FR__.push(String(m).slice(0,1200)); }); });
  const t0 = Date.now();
  try { const resp = await page.goto(BASE+route,{waitUntil:'domcontentloaded',timeout:NT}); r.navStatus = resp?resp.status():null; if(HARD){await page.reload({waitUntil:'domcontentloaded',timeout:NT});} await page.waitForTimeout(SETTLE); }
  catch(e) { r.navError = rd(String(e.message||e)).slice(0,700); }
  r.durationMs = Date.now()-t0;
  try { r.finalUrl = page.url(); } catch(e) {}
  try { const j = await page.evaluate(function(){return window.__FR__||[];}); r.rejections = j.map(rd); } catch(e) {}
  await page.close();
  return r;
}
function analyze(recs) {
  const s = { appConsoleErrors: [], appConsoleWarnings: [], pageErrors: [], rejections: [], failedRequests: [], http4xx: [], http5xx: [], redirects: [], websocketErrors: [], externalEvents: [], slowApi: [], staleEndpoints: [], chunkErrors: [], corsErrors: [], mixedContent: [], devTelemetry: [] };
  for (const r of recs) {
    for (const c of r.console) {
      const ext = c.url && EXT.test(c.url);
      const it = Object.assign({route:r.route}, c);
      if (c.type==='error') { if(ext){s.externalEvents.push(it);}else{s.appConsoleErrors.push(it);} }
      else if (c.type==='warning') { if(ext){s.externalEvents.push(it);}else{s.appConsoleWarnings.push(it);} }
      const t=(c.text||'').toLowerCase();
      if(t.indexOf('[dev/test]')!==-1||t.indexOf('dev telemetry')!==-1) s.devTelemetry.push(it);
      if(t.indexOf('cors')!==-1||t.indexOf('access-control-allow-origin')!==-1) s.corsErrors.push(it);
      if(t.indexOf('mixed content')!==-1) s.mixedContent.push(it);
      if(t.indexOf('dynamically imported module')!==-1||t.indexOf('loading chunk')!==-1||t.indexOf('module script failed')!==-1) s.chunkErrors.push(it);
    }
    for (const e of r.pageErrors) s.pageErrors.push(Object.assign({route:r.route},e));
    for (const u of r.rejections) s.rejections.push({route:r.route,message:u});
    for (const f of r.failed) { const it=Object.assign({route:r.route},f); if(EXT.test(f.url)){s.externalEvents.push(it);}else{s.failedRequests.push(it);} }
    for (const x of r.responses) {
      const own = so(x.url,BASE) || x.url.indexOf('/api/')!==-1;
      if(x.status>=400&&x.status<500) s.http4xx.push(Object.assign({route:r.route,appOrigin:own},x));
      else if(x.status>=500) s.http5xx.push(Object.assign({route:r.route,appOrigin:own},x));
      if(x.durationMs!=null&&x.durationMs>1000&&x.url.indexOf('/api/')!==-1) s.slowApi.push({route:r.route,url:x.url,durationMs:x.durationMs});
      if(/localhost|127\.0\.0\.1|vyomquant-alb/i.test(x.url)||/^http:\/\//i.test(x.url)) s.staleEndpoints.push({route:r.route,url:x.url});
    }
    for (const d of r.redirects) s.redirects.push(Object.assign({route:r.route},d));
    for (const w of r.websockets) { const er=w.events.filter(function(e){return e.type==='error';}); if(er.length) s.websocketErrors.push({route:r.route,url:w.url,errors:er}); }
  }
  return s;
}
(async function(){
  console.log('[bf] PASS='+PASS+' BASE='+BASE+' hard='+HARD+' soak='+SOAK);
  const br = await chromium.launch({headless:true,args:['--disable-extensions','--disable-plugins','--no-first-run']});
  const ctx = await br.newContext({viewport:{width:1600,height:1000}});
  const recs = [];
  for (const rt of ROUTES) {
    const r = await cap(ctx, rt);
    const ce = r.console.filter(function(c){return c.type==='error';}).length;
    console.log('[bf] '+rt+' status='+r.navStatus+' cErr='+ce+' pErr='+r.pageErrors.length+' rFail='+r.failed.length+' final='+r.finalUrl);
    recs.push(r);
  }
  const soak = {cycles:SOAK,errors:[],metrics:null};
  if (SOAK>0) {
    const p = await ctx.newPage();
    p.on('console',function(m){if(m.type()==='error')soak.errors.push(rd(m.text()).slice(0,400));});
    p.on('pageerror',function(e){soak.errors.push('PAGEERROR: '+rd(e.message).slice(0,400));});
    for (let i=0;i<SOAK;i++) { for (const rt of ['/','/signin','/app/dashboard']) {
      try { await p.goto(BASE+rt,{waitUntil:'domcontentloaded',timeout:NT}); await p.waitForTimeout(250); }
      catch(e){ soak.errors.push('NAV_FAIL '+rt+': '+rd(String(e.message)).slice(0,250)); } } }
    try { soak.metrics = await p.evaluate(function(){return {heapUsed:(performance.memory||{}).usedJSHeapSize||null};}); } catch(e){}
    await p.close();
    console.log('[bf] soak cycles='+SOAK+' errors='+soak.errors.length);
  }
  const sum = analyze(recs);
  const verdict = {};
  for (const k of Object.keys(sum)) verdict[k] = sum[k].length;
  const rep = { meta:{pass:PASS,base:BASE,hardRefresh:HARD,soakCycles:SOAK,capturedAt:new Date().toISOString(),authenticated:false,authNote:'No production test credentials available; protected routes captured unauthenticated.',routesTested:ROUTES.length}, verdict:verdict, summary:sum, soak:soak, routes:recs };
  fs.mkdirSync('reports',{recursive:true});
  fs.writeFileSync('reports/browser_forensic_pass'+PASS+'.json', JSON.stringify(rep,null,2), 'utf8');
  console.log('');
  console.log('[bf] ===== VERDICT =====');
  for (const k of Object.keys(verdict)) console.log('[bf] '+k+': '+verdict[k]);
  await ctx.close(); await br.close();
})().catch(function(e){console.error('[bf] FATAL',e);process.exit(1);});
