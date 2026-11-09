import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, writeFile, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
import { validateRuntime, validEmbedOrigin, runtimeRequestOrigin, safeEnvironment, downloadRuntime, createRuntimeManager } from '../src/runtime.js';
const bytes=Buffer.alloc(2048,42);
const spec={version:'0.9.5',url:'https://github.com/Lee-Eee-Eee/API-MRI/releases/download/v0.9.5/API-MRI-0.9.5-windows-x64.zip',sha256:createHash('sha256').update(bytes).digest('hex'),size_bytes:bytes.length};

test('authenticated desktop bridge may omit Origin, Web must match the requested embed origin',()=>{
  assert.equal(runtimeRequestOrigin({}, {embedOrigin:'dsh-app://app'}),'dsh-app://app');
  assert.equal(runtimeRequestOrigin({origin:'http://127.0.0.1:3088'}, {embedOrigin:'http://127.0.0.1:3088'}),'http://127.0.0.1:3088');
  for(const [headers,payload] of [
    [{},{}], [{},{embedOrigin:'http://127.0.0.1:3088'}],
    [{origin:'http://127.0.0.1:3088'},{embedOrigin:'dsh-app://app'}],
    [{origin:'https://evil.test'},{embedOrigin:'dsh-app://app'}],
    [{},{embedOrigin:'dsh-app://evil'}],
  ])assert.throws(()=>runtimeRequestOrigin(headers,payload));
});

test('runtime pins version, repository, digest, size, and embed origin',()=>{
  assert.equal(validateRuntime(spec),spec);
  for(const change of [{version:'0.8.0'},{sha256:null},{size_bytes:Infinity},{url:'https://evil.test/a.zip'}])assert.throws(()=>validateRuntime({...spec,...change}));
  assert.equal(validEmbedOrigin('dsh-app://app'),'dsh-app://app');
  for(const origin of ['null','https://evil.test','dsh-app://evil','http://127.0.0.1:99999'])assert.throws(()=>validEmbedOrigin(origin));
  assert.deepEqual(safeEnvironment({PATH:'test',OPENAI_API_KEY:'never-copy',DSH_TOKEN:'never-copy'}),{PATH:'test',PYTHONUTF8:'1'});
  assert.equal(safeEnvironment({HTTPS_PROXY:'http://127.0.0.1:1234',OPENAI_API_KEY:'never-copy'}).HTTPS_PROXY,'http://127.0.0.1:1234');
});

test('download streams exact bytes, rejects tampering and off-host redirects',async()=>{
  const folder=await mkdtemp(path.join(tmpdir(),'api-mri-download-'));
  const good=path.join(folder,'good.zip');let progress;
  await downloadRuntime(spec,good,v=>progress=v,async()=>new Response(bytes));
  assert.deepEqual(await readFile(good),bytes);assert.equal(progress.downloaded_bytes,bytes.length);
  await assert.rejects(downloadRuntime({...spec,sha256:'a'.repeat(64)},path.join(folder,'bad.zip'),()=>{},async()=>new Response(bytes)),/SHA-256/);
  await assert.rejects(downloadRuntime(spec,path.join(folder,'redirect.zip'),()=>{},async()=>new Response(null,{status:302,headers:{location:'https://evil.test/run.zip'}})),/跳转来源/);
});

test('installed runtime starts once, reports ready URL, and owns its child only',async()=>{
  const root=await mkdtemp(path.join(tmpdir(),'api-mri-runtime-'));
  const target=path.join(root,'0.9.5');await mkdir(path.join(target,'API-MRI-0.9.5-windows-x64'),{recursive:true});
  await writeFile(path.join(target,'API-MRI-0.9.5-windows-x64','API-MRI.exe'),'fixture');
  await writeFile(path.join(target,'runtime-receipt.json'),JSON.stringify(spec));
  const manifest=path.join(root,'manifest.json');await writeFile(manifest,JSON.stringify(spec));
  await mkdir(path.join(root,'data'));await writeFile(path.join(root,'data','.portable-session.json'),JSON.stringify({version:spec.version,installation_id:'fixture-install',port:19765,token:'ab'.repeat(32)}));
  let starts=0,kills=0,args;
  const manager=createRuntimeManager({root,platform:'win32',arch:'x64',manifestUrl:manifest,
    spawnChild(exe,argv,options){starts++;args=argv;assert.equal(options.windowsHide,true);const child=new EventEmitter();child.kill=()=>kills++;return child;},
    fetcher:async(url)=>starts?new Response(JSON.stringify(String(url).endsWith('bootstrap')?{version:'0.9.5',embedding:{origins:['dsh-app://app']}}:{version:spec.version,installation_id:'fixture-install'})):new Response(null,{status:503})});
  await manager.start('dsh-app://app');await manager.start('dsh-app://app');
  for(let i=0;i<40&&manager.snapshot().phase!=='ready';i++)await new Promise(r=>setTimeout(r,10));
  assert.equal(starts,1);assert.equal(manager.snapshot().url,'http://127.0.0.1:19765');
  assert.ok(args.includes('--headless'));assert.ok(args.includes('--embed-origin'));
  manager.dispose();assert.equal(kills,1);
});

function fixtureZip(name,content) {
  const filename=Buffer.from(name), data=Buffer.from(content);
  let crc=0xffffffff;
  for(const byte of data){crc^=byte;for(let bit=0;bit<8;bit++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}
  crc=(crc^0xffffffff)>>>0;
  const local=Buffer.alloc(30);local.writeUInt32LE(0x04034b50);local.writeUInt16LE(20,4);local.writeUInt32LE(crc,14);
  local.writeUInt32LE(data.length,18);local.writeUInt32LE(data.length,22);local.writeUInt16LE(filename.length,26);
  const central=Buffer.alloc(46);central.writeUInt32LE(0x02014b50);central.writeUInt16LE(20,4);central.writeUInt16LE(20,6);
  central.writeUInt32LE(crc,16);central.writeUInt32LE(data.length,20);central.writeUInt32LE(data.length,24);central.writeUInt16LE(filename.length,28);
  const end=Buffer.alloc(22);end.writeUInt32LE(0x06054b50);end.writeUInt16LE(1,8);end.writeUInt16LE(1,10);
  end.writeUInt32LE(central.length+filename.length,12);end.writeUInt32LE(local.length+filename.length+data.length,16);
  return Buffer.concat([local,filename,data,central,filename,end]);
}

test('fresh Windows install verifies and extracts the ZIP before starting; traversal never escapes', {skip:process.platform!=='win32',timeout:450000},async()=>{
  for(const malicious of [false,true]) {
    const root=await mkdtemp(path.join(tmpdir(),'api-mri-fresh-'));
    const payload=Buffer.alloc(2048,37),zip=fixtureZip(malicious?'../escaped.exe':'API-MRI-0.9.5-windows-x64/API-MRI.exe',payload);
    const fixture={...spec,sha256:createHash('sha256').update(zip).digest('hex'),size_bytes:zip.length};
    const manifest=path.join(root,'manifest.json');await writeFile(manifest,JSON.stringify(fixture));
    await mkdir(path.join(root,'data'));await writeFile(path.join(root,'data','.portable-session.json'),JSON.stringify({version:spec.version,installation_id:'fixture-install',port:19765,token:'ac'.repeat(32)}));
    let starts=0;
    const manager=createRuntimeManager({root,manifestUrl:manifest,
      spawnChild(){starts++;const c=new EventEmitter();c.kill=()=>{};return c;},
      fetcher:async(url)=>String(url).endsWith('.zip')?new Response(zip):starts?new Response(JSON.stringify(String(url).endsWith('bootstrap')?{version:'0.9.5',embedding:{origins:['dsh-app://app']}}:{version:spec.version,installation_id:'fixture-install'})):new Response(null,{status:503})});
    await manager.start('dsh-app://app');
    // A cold Windows runner may spend over 30 seconds starting .NET/Defender.
    // Match the extractor's 180-second bound instead of ending mid-extraction.
    const deadline=Date.now()+210000;
    while(Date.now()<deadline&&!['error','ready'].includes(manager.snapshot().phase))await new Promise(r=>setTimeout(r,50));
    if(malicious){assert.equal(manager.snapshot().phase,'error');assert.equal(starts,0);}
    else{assert.equal(manager.snapshot().phase,'ready');assert.equal(starts,1);
      assert.deepEqual(await readFile(path.join(root,'0.9.5','API-MRI-0.9.5-windows-x64','API-MRI.exe')),payload);}
    manager.dispose();
  }
});

async function installedFixture(manifestSpec=spec) {
  const root=await mkdtemp(path.join(tmpdir(),'api-mri-reopen-'));
  const installed=path.join(root,spec.version,`API-MRI-${spec.version}-windows-x64`);
  await mkdir(installed,{recursive:true});await writeFile(path.join(installed,'API-MRI.exe'),'fixture');
  await writeFile(path.join(root,spec.version,'runtime-receipt.json'),JSON.stringify({version:spec.version,sha256:spec.sha256}));
  const manifest=path.join(root,'manifest.json');await writeFile(manifest,JSON.stringify(manifestSpec));
  await mkdir(path.join(root,'data'));
  await writeFile(path.join(root,'data','.portable-session.json'),JSON.stringify({version:spec.version,installation_id:'reopen-fixture',port:19766,token:'ad'.repeat(32)}));
  return {root,manifestUrl:manifest,platform:'win32',arch:'x64',pollMs:2,startupTimeoutMs:100};
}
function healthResponse(url) {
  return new Response(JSON.stringify(String(url).endsWith('bootstrap')
    ?{version:spec.version,embedding:{origins:['dsh-app://app']}}
    :{version:spec.version,installation_id:'reopen-fixture'}));
}
async function waitPhase(manager,phase) {
  for(let i=0;i<100&&manager.snapshot().phase!==phase;i++)await new Promise(resolve=>setTimeout(resolve,2));
  assert.equal(manager.snapshot().phase,phase,manager.snapshot().error);
}

test('unbound source manifest starts a verified local install while GitHub is unavailable',async()=>{
  const fixture=await installedFixture({...spec,sha256:null,size_bytes:null});let starts=0;
  const manager=createRuntimeManager({...fixture,
    fetcher:async url=>{assert.equal(new URL(url).protocol,'http:','local installed runtime must not need Release metadata');return starts?healthResponse(url):new Response(null,{status:503});},
    spawnChild(){starts++;const child=new EventEmitter();child.kill=()=>{};return child;},
  });
  try {await manager.start('dsh-app://app');await waitPhase(manager,'ready');assert.equal(starts,1);}
  finally {manager.dispose();}
});

test('an authenticated healthy instance is reused without spawning or owning its process',async()=>{
  const fixture=await installedFixture();
  const manager=createRuntimeManager({...fixture,fetcher:async url=>healthResponse(url),spawnChild(){assert.fail('healthy instance must be reused');}});
  await manager.start('dsh-app://app');await waitPhase(manager,'ready');
  assert.equal(manager.snapshot().reused,true);assert.equal(manager.snapshot().url,'http://127.0.0.1:19766');
  manager.dispose();assert.equal(manager.snapshot().phase,'closed');
});

test('bootstrap slower than two seconds can start and survive repeated status checks',{timeout:20000},async()=>{
  const fixture=await installedFixture();let starts=0,bootstrapCalls=0;
  const manager=createRuntimeManager({...fixture,pollMs:2500,startupTimeoutMs:2500,
    fetcher:async(url,{signal}={})=>{
      if(!starts)return new Response(null,{status:503});
      assert.ok(signal instanceof AbortSignal);
      if(String(url).endsWith('bootstrap')) {
        bootstrapCalls++;
        // Respect the supplied deadline: the previous two-second signal aborts
        // this request, just as a slow packaged backend does during startup.
        await delay(2100,undefined,{signal});
      }
      return healthResponse(url);
    },
    spawnChild(){starts++;const child=new EventEmitter();child.kill=()=>{};return child;},
  });
  try {
    await manager.start('dsh-app://app');
    const deadline=Date.now()+8000;
    while(Date.now()<deadline&&!['ready','error'].includes(manager.snapshot().phase))await delay(10);
    assert.equal(manager.snapshot().phase,'ready',manager.snapshot().error);
    for(let i=0;i<2;i++)assert.equal((await manager.status()).phase,'ready');
    assert.equal(starts,1);
    assert.equal(bootstrapCalls,3);
    assert.equal(manager.snapshot().url,'http://127.0.0.1:19766');
  } finally {manager.dispose();}
});

test('a launcher that exits zero after forwarding to a live instance is successful',async()=>{
  const fixture=await installedFixture();let starts=0,kills=0;
  const manager=createRuntimeManager({...fixture,
    fetcher:async url=>{if(!starts)return new Response(null,{status:503});await new Promise(resolve=>setImmediate(resolve));return healthResponse(url);},
    spawnChild(){starts++;const child=new EventEmitter();child.kill=()=>kills++;queueMicrotask(()=>child.emit('exit',0));return child;},
  });
  await manager.start('dsh-app://app');await waitPhase(manager,'ready');
  assert.equal(manager.snapshot().reused,true);manager.dispose();assert.equal(kills,0);
});

test('reopening rechecks cached readiness and ignores an older owned process exit',async()=>{
  const fixture=await installedFixture();let healthy=false,starts=0,kills=0;
  const manager=createRuntimeManager({...fixture,
    fetcher:async url=>healthy?healthResponse(url):new Response(null,{status:503}),
    spawnChild(){starts++;healthy=true;const child=new EventEmitter();child.kill=()=>{kills++;queueMicrotask(()=>child.emit('exit',1));};return child;},
  });
  try {
    await manager.start('dsh-app://app');await waitPhase(manager,'ready');healthy=false;
    assert.equal((await manager.status()).phase,'idle');
    await manager.start('dsh-app://app');await waitPhase(manager,'ready');
    assert.equal(starts,2);assert.equal(kills,1);assert.equal(manager.snapshot().error,null);
  }finally{manager.dispose();}
});

test('startup timeout kills only its owned process and a later retry can become ready',async()=>{
  const fixture=await installedFixture();let starts=0,kills=0;
  const manager=createRuntimeManager({...fixture,startupTimeoutMs:12,
    fetcher:async url=>starts>=2?healthResponse(url):new Response(null,{status:503}),
    spawnChild(){starts++;const child=new EventEmitter();child.kill=()=>{kills++;queueMicrotask(()=>child.emit('exit',1));};return child;},
  });
  try {
    await manager.start('dsh-app://app');await waitPhase(manager,'error');
    assert.match(manager.snapshot().error,/超时/);assert.equal(kills,1);
    await manager.start('dsh-app://app');await waitPhase(manager,'ready');assert.equal(starts,2);
  }finally{manager.dispose();}
});

test('a port returning a different authenticated identity is never accepted as ready',async()=>{
  const fixture=await installedFixture();let starts=0;
  const manager=createRuntimeManager({...fixture,
    fetcher:async()=>new Response(JSON.stringify({version:spec.version,installation_id:'different-installation'})),
    spawnChild(){starts++;const child=new EventEmitter();child.kill=()=>{};queueMicrotask(()=>child.emit('exit',1));return child;},
  });
  try {await manager.start('dsh-app://app');await waitPhase(manager,'error');assert.equal(starts,1);}
  finally{manager.dispose();}
});
