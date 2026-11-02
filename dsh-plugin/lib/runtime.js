import { createHash, randomBytes } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { readFile, mkdir, rename, rm, stat } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
const exec = promisify(execFile);
const VERSION = '0.9.0';
class RuntimeError extends Error {}

export function validateRuntime(value) {
  const expected=`https://github.com/Lee-Eee-Eee/API-MRI/releases/download/v${VERSION}/API-MRI-${VERSION}-windows-x64.zip`;
  if(value?.version!==VERSION || value?.url!==expected || !/^[a-f0-9]{64}$/.test(value?.sha256 || '')
    || !Number.isSafeInteger(value?.size_bytes) || value.size_bytes<1000 || value.size_bytes>650*1024*1024)
    throw new RuntimeError('此开发包尚未绑定运行程序。请使用 Release 中的 DSH 插件包，或连接已启动的 API-MRI。');
  return value;
}

export function safeEnvironment(base=process.env) {
  const env={};
  for(const key of ['SystemRoot','SYSTEMROOT','WINDIR','PATH','Path','PATHEXT','TEMP','TMP','LOCALAPPDATA','APPDATA','USERPROFILE'])
    if(base[key])env[key]=base[key];
  env.PYTHONUTF8='1'; return env;
}

export function validEmbedOrigin(value) {
  if(value==='dsh-app://app')return value;
  if(typeof value==='string' && /^https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?$/.test(value)) {
    const u=new URL(value); if(u.port==='' || Number(u.port)>0&&Number(u.port)<65536)return u.origin;
  }
  throw new Error('DSH 来源无效。');
}

export async function downloadRuntime(spec, destination, progress, fetcher=fetch) {
  validateRuntime(spec);
  let url=spec.url, response;
  for(let i=0;i<4;i++) {
    const u=new URL(url);
    if(u.protocol!=='https:' || !['github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'].includes(u.hostname))
      throw new Error('下载跳转来源无效。');
    response=await fetcher(url,{redirect:'manual',signal:AbortSignal.timeout(120000)});
    if([301,302,303,307,308].includes(response.status)){const next=response.headers.get('location'); await response.body?.cancel();if(!next)throw new Error('下载跳转为空。');url=new URL(next,url).href;continue;}
    break;
  }
  if(response?.status!==200 || !response.body)throw new Error('无法下载运行程序，请重试。');
  const hash=createHash('sha256'), stream=createWriteStream(destination,{flags:'wx'});let size=0;
  stream.on('error',()=>{});
  // Await every write: the stream cannot buffer a whole portable archive.
  try {
    for await(const chunk of response.body) {
      size+=chunk.length;if(size>spec.size_bytes)throw new Error('运行包大小不符。');hash.update(chunk);
      await new Promise((resolve,reject)=>stream.write(chunk,e=>e?reject(e):resolve()));
      progress({phase:'downloading',downloaded_bytes:size,total_bytes:spec.size_bytes});
    }
    await new Promise((resolve,reject)=>stream.end(e=>e?reject(e):resolve()));
    if(size!==spec.size_bytes || hash.digest('hex')!==spec.sha256)throw new RuntimeError('运行包 SHA-256 校验失败。');
  } catch(e){stream.destroy();throw e;}
}

// .NET inspects entries before extraction, including Unix symlink flags.
const EXTRACT = String.raw`
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive=[IO.Compression.ZipFile]::OpenRead($env:APIMRI_ARCHIVE)
try {
  if($archive.Entries.Count -gt 30000){throw 'Too many archive entries'}
  $root=[IO.Path]::GetFullPath($env:APIMRI_STAGE)+[IO.Path]::DirectorySeparatorChar
  [long]$total=0
  foreach($entry in $archive.Entries){
    $name=$entry.FullName
    if($name.Contains('\') -or $name.Contains(':') -or $name.StartsWith('/') -or $name.Split('/') -contains '..'){throw 'Invalid archive path'}
    $target=[IO.Path]::GetFullPath([IO.Path]::Combine($root,$name))
    if(-not $target.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){throw 'Archive escaped destination'}
    if((($entry.ExternalAttributes -shr 16) -band 61440) -eq 40960){throw 'Symlink in archive'}
    $total+=$entry.Length
    if($total -gt 3221225472){throw 'Expanded archive is too large'}
  }
} finally {$archive.Dispose()}
[IO.Compression.ZipFile]::ExtractToDirectory($env:APIMRI_ARCHIVE,$env:APIMRI_STAGE)
`;

export function createRuntimeManager({root, platform=process.platform, arch=process.arch, manifestUrl=new URL('./runtime-manifest.json',import.meta.url),
  fetcher=fetch, spawnChild=spawn, run=exec}={}) {
  root ||= path.join(process.env.LOCALAPPDATA || path.join(homedir(),'.local','share'),'API-MRI','dsh-runtime');
  let state={phase:'idle',version:VERSION}, pending, child, disposed=false;
  const snapshot=()=>({...state});
  const update=v=>{state={...state,...v};};
  async function install() {
    if(platform!=='win32'||arch!=='x64')throw new RuntimeError('自动安装目前提供 Windows x64 运行包；可连接自行部署的 API-MRI。');
    let supplied=JSON.parse(await readFile(manifestUrl,'utf8'));
    if(supplied.sha256===null) {
      const url=`https://github.com/Lee-Eee-Eee/API-MRI/releases/download/v${VERSION}/api-mri-runtime.json`;
      const meta=await fetcher(url,{redirect:'follow',signal:AbortSignal.timeout(30000)});
      if(!meta.ok || Number(meta.headers.get('content-length')||0)>16384)throw new Error('此版本尚未发布运行包。');
      const text=await meta.text();if(text.length>16384)throw new Error('运行清单过大。');
      supplied=JSON.parse(text);
    }
    const spec=validateRuntime(supplied);
    const target=path.join(root,VERSION),exe=path.join(target,`API-MRI-${VERSION}-windows-x64`,'API-MRI.exe');
    try {
      const receipt=JSON.parse(await readFile(path.join(target,'runtime-receipt.json'),'utf8'));
      if(receipt.sha256===spec.sha256 && (await stat(exe)).isFile())return exe;
    }catch{}
    await mkdir(root,{recursive:true});
    const stage=path.join(root,`.install-${randomBytes(8).toString('hex')}`),archive=stage+'.zip';
    if(path.dirname(path.resolve(stage))!==path.resolve(root) || !path.basename(stage).startsWith('.install-'))throw new Error('安装目录无效。');
    try {
      update({phase:'downloading',downloaded_bytes:0,total_bytes:spec.size_bytes});
      await downloadRuntime(spec,archive,update,fetcher);
      if(disposed)throw new Error('插件已关闭。');
      update({phase:'extracting'});
      await run('powershell.exe',['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(EXTRACT,'utf16le').toString('base64')],
        {windowsHide:true,timeout:180000,env:{...safeEnvironment(),APIMRI_ARCHIVE:archive,APIMRI_STAGE:stage}});
      const stagedExe=path.join(stage,`API-MRI-${VERSION}-windows-x64`,'API-MRI.exe');
      if(!(await stat(stagedExe)).isFile())throw new Error('运行包缺少程序入口。');
      const {writeFile}=await import('node:fs/promises');
      await writeFile(path.join(stage,'runtime-receipt.json'),JSON.stringify({version:VERSION,sha256:spec.sha256}));
      // Do not replace an existing installation or remove paths from the archive.
      await rename(stage,target);
      return exe;
    }finally{
      await rm(archive,{force:true});await rm(stage,{recursive:true,force:true});
    }
  }
  async function start(origin) {
    origin=validEmbedOrigin(origin);
    if(pending)return snapshot();
    if(state.phase==='ready') {
      if(state.embed_origin!==origin)throw new Error('运行程序已绑定另一 DSH 来源，请重启插件后连接。');
      return snapshot();
    }
    pending=(async()=>{
      const exe=await install();if(disposed)return;
      update({phase:'starting',embed_origin:origin});
      // Each plugin runtime owns its process and data. Port 0 avoids accidentally
      // terminating or changing an independently started API-MRI workbench.
      child=spawnChild(exe,['--headless','--no-browser','--port','0','--data-dir',path.join(root,'data'),'--embed-origin',origin],
        {windowsHide:true,stdio:'ignore',env:safeEnvironment()});
      child.once('error',()=>update({phase:'error',error:'运行程序无法启动。'}));
      child.once('exit',()=>{if(!disposed)update({phase:'error',error:'运行程序已退出，可重试。'});child=null;});
      for(let i=0;i<180;i++) {
        if(disposed || state.phase==='error')return;
        try {
          const record=JSON.parse(await readFile(path.join(root,'data','.portable-session.json'),'utf8'));
          if(Number.isInteger(record.port) && record.port>=1024 && record.port<=65535 && /^[a-f0-9]+$/.test(record.token || '')) {
            const url=`http://127.0.0.1:${record.port}`;
            const identity=await fetcher(url+'/api/portable/instance',{headers:{'X-API-MRI-Instance':record.token},signal:AbortSignal.timeout(2000)});
            if(!identity.ok)continue;
            const r=await fetcher(url+'/api/bootstrap',{signal:AbortSignal.timeout(2000)});
            const b=await r.json();
            if(r.ok && b.version===VERSION && b.embedding?.origins?.includes(origin)) {
              update({phase:'ready',url,error:null});return;
            }
          }
        }catch{}
        await new Promise(resolve=>setTimeout(resolve,500));
      }
      child?.kill();update({phase:'error',error:'启动超时，请重试。'});
    })().catch(e=>update({phase:'error',error:e instanceof RuntimeError?e.message:'下载或启动未完成。检查网络后重试，或连接已启动的 API-MRI。'})).finally(()=>{pending=null;});
    return snapshot();
  }
  return {snapshot,start,dispose(){disposed=true;child?.kill();}};
}
