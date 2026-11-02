import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, writeFile, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { validateRuntime, validEmbedOrigin, safeEnvironment, downloadRuntime, createRuntimeManager } from '../src/runtime.js';
const bytes=Buffer.alloc(2048,42);
const spec={version:'0.9.0',url:'https://github.com/Lee-Eee-Eee/API-MRI/releases/download/v0.9.0/API-MRI-0.9.0-windows-x64.zip',sha256:createHash('sha256').update(bytes).digest('hex'),size_bytes:bytes.length};

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
  const target=path.join(root,'0.9.0');await mkdir(path.join(target,'API-MRI-0.9.0-windows-x64'),{recursive:true});
  await writeFile(path.join(target,'API-MRI-0.9.0-windows-x64','API-MRI.exe'),'fixture');
  await writeFile(path.join(target,'runtime-receipt.json'),JSON.stringify(spec));
  const manifest=path.join(root,'manifest.json');await writeFile(manifest,JSON.stringify(spec));
  await mkdir(path.join(root,'data'));await writeFile(path.join(root,'data','.portable-session.json'),JSON.stringify({port:19765,token:'ab'.repeat(32)}));
  let starts=0,kills=0,args;
  const manager=createRuntimeManager({root,platform:'win32',arch:'x64',manifestUrl:manifest,
    spawnChild(exe,argv,options){starts++;args=argv;assert.equal(options.windowsHide,true);const child=new EventEmitter();child.kill=()=>kills++;return child;},
    fetcher:async(url)=>new Response(JSON.stringify(String(url).endsWith('bootstrap')?{version:'0.9.0',embedding:{origins:['dsh-app://app']}}:{}))});
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

test('fresh Windows install verifies and extracts the ZIP before starting; traversal never escapes', {skip:process.platform!=='win32'},async()=>{
  for(const malicious of [false,true]) {
    const root=await mkdtemp(path.join(tmpdir(),'api-mri-fresh-'));
    const payload=Buffer.alloc(2048,37),zip=fixtureZip(malicious?'../escaped.exe':'API-MRI-0.9.0-windows-x64/API-MRI.exe',payload);
    const fixture={...spec,sha256:createHash('sha256').update(zip).digest('hex'),size_bytes:zip.length};
    const manifest=path.join(root,'manifest.json');await writeFile(manifest,JSON.stringify(fixture));
    await mkdir(path.join(root,'data'));await writeFile(path.join(root,'data','.portable-session.json'),JSON.stringify({port:19765,token:'ac'.repeat(32)}));
    let starts=0;
    const manager=createRuntimeManager({root,manifestUrl:manifest,
      spawnChild(){starts++;const c=new EventEmitter();c.kill=()=>{};return c;},
      fetcher:async(url)=>String(url).endsWith('.zip')?new Response(zip):new Response(JSON.stringify(String(url).endsWith('bootstrap')?{version:'0.9.0',embedding:{origins:['dsh-app://app']}}:{}))});
    await manager.start('dsh-app://app');
    for(let i=0;i<600&&!['error','ready'].includes(manager.snapshot().phase);i++)await new Promise(r=>setTimeout(r,50));
    if(malicious){assert.equal(manager.snapshot().phase,'error');assert.equal(starts,0);}
    else{assert.equal(manager.snapshot().phase,'ready');assert.equal(starts,1);
      assert.deepEqual(await readFile(path.join(root,'0.9.0','API-MRI-0.9.0-windows-x64','API-MRI.exe')),payload);}
    manager.dispose();
  }
});
