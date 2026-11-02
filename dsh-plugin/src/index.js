import { createRuntimeManager, validEmbedOrigin } from './runtime.js';
export const name = 'dsh-api-mri';
export const inject = ['webServer','connection'];
export function apply(ctx) {
  const runtime=createRuntimeManager();
  ctx.effect(()=>()=>runtime.dispose());
  ctx.effect(()=>ctx.webServer.register({kind:'exact',path:'/api-mri/runtime',handler:async(req,res)=>{
    res.setHeader('Content-Type','application/json; charset=utf-8');res.setHeader('Cache-Control','no-store');
    const send=(status,value)=>{res.statusCode=status;res.end(JSON.stringify(value));};
    if(ctx.connection.requestRejection(req)!==undefined)return send(403,{error:'forbidden'});
    if(req.method==='GET')return send(200,runtime.snapshot());
    if(req.method!=='POST')return send(405,{error:'method not allowed'});
    try {
      const origin=validEmbedOrigin(req.headers.origin);
      let size=0;for await(const chunk of req){size+=chunk.length;if(size>1024)return send(413,{error:'body too large'});}
      return send(200,await runtime.start(origin));
    }catch{return send(400,{error:'无法启动，请使用 DSH 桌面版或回环地址。'});}
  }}));
}
