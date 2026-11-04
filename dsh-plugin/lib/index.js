import { createRuntimeManager, runtimeRequestOrigin } from './runtime.js';
import { createModelBridge, readJson, BRIDGE_LIMITS } from './model-bridge.js';
export const name = 'dsh-api-mri';
export const inject = ['webServer','connection','llm'];
export function apply(ctx) {
  const runtime=createRuntimeManager();
  const models=createModelBridge(ctx.llm);
  ctx.effect(()=>()=>runtime.dispose());
  ctx.effect(()=>()=>{void models.dispose();});
  ctx.effect(()=>ctx.webServer.register({kind:'exact',path:'/api/api-mri/runtime',handler:async(req,res)=>{
    res.setHeader('Content-Type','application/json; charset=utf-8');res.setHeader('Cache-Control','no-store');
    const send=(status,value)=>{res.statusCode=status;res.end(JSON.stringify(value));};
    if(ctx.connection.requestRejection(req)!==undefined)return send(403,{error:'forbidden'});
    if(req.method==='GET')return send(200,await runtime.status());
    if(req.method!=='POST')return send(405,{error:'method not allowed'});
    try {
      const chunks=[];let size=0;for await(const chunk of req){size+=chunk.length;if(size>1024)return send(413,{error:'body too large'});chunks.push(Buffer.from(chunk));}
      const origin=runtimeRequestOrigin(req.headers,JSON.parse(Buffer.concat(chunks).toString('utf8')));
      return send(200,await runtime.start(origin));
    }catch{return send(400,{error:'无法启动，请使用 DSH 桌面版或回环地址。'});}
  }}));
  for (const [path, method] of [['/api/api-mri/models','GET'],['/api/api-mri/lease','POST']]) {
    ctx.effect(()=>ctx.webServer.register({kind:'exact',path,handler:async(req,res)=>{
      const send=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
      if(ctx.connection.requestRejection(req)!==undefined)return send(403,{error:'forbidden'});
      if(req.method!==method)return send(405,{error:'method not allowed'});
      try {
        if(method==='GET')return send(200,{models:await models.catalog(),apiBase:await models.address(),limits:BRIDGE_LIMITS,transport:'dsh-adapter'});
        const body=await readJson(req,4096);runtimeRequestOrigin(req.headers,body);
        return send(200,await models.issue(body));
      }catch(e){return send(e.safeForClient?e.status:400,{error:e.safeForClient?e.message:'无法读取 DSH 模型或创建调用授权，请刷新后重试。'});}
    }}));
  }
}
