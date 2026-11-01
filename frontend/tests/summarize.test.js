import { test } from 'node:test'
import assert from 'node:assert/strict'
import { handleSSEStream, summarizeVideo, fetchSummaryResult } from '../src/api/summarize.js'

const storage=new Map()
global.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}

test('SSE 在 UTF-8、CRLF 和 JSON 换行被分块时仍还原字幕与摘要', async () => {
  const source=': ping\r\nevent: subtitle\r\ndata: {"full_text":\r\ndata: "学习"}\r\n\r\nevent: summary\r\ndata: "第一行\\n第二行"\r\n\r\nevent: done\r\ndata: [DONE]\r\n\r\n'
  const bytes=new TextEncoder().encode(source)
  const response=new Response(new ReadableStream({start(controller){
    for(let index=0;index<bytes.length;index+=3)controller.enqueue(bytes.slice(index,index+3))
    controller.close()
  }}))
  const events=[]
  await handleSSEStream(response,{
    subtitle:data=>events.push(JSON.parse(data)),
    summary:data=>events.push(JSON.parse(data)),
    done:()=>events.push('done'),
  })
  assert.deepEqual(events,[{full_text:'学习'},'第一行\n第二行','done'])
})

test('SSE 中途断流不能当作生成完成', async () => {
  await assert.rejects(
    ()=>handleSSEStream(new Response('event: summary\ndata: "半句话"\n\n'),{}),
    /中断/,
  )
})

test('AI 服务返回非 JSON 网关错误时提示重试而非 JSON 解析异常',async()=>{
  global.fetch=async()=>new Response('Service Unavailable',{status:503})
  await assert.rejects(()=>summarizeVideo('https://example.com/video'),/503.*重试/)
})

test('AI 请求附带本服务 Token，并接收额度事件',async()=>{
  storage.set('saveany_token','my-session')
  global.fetch=async(path,options)=>{
    assert.equal(path,'/api/summarize')
    assert.equal(options.headers.Authorization,'Bearer my-session')
    return new Response('event: quota\ndata: {"summary":{"remaining":2}}\n\nevent: done\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}})
  }
  let snapshot
  await summarizeVideo('https://example.com/video','zh',{quota:data=>snapshot=JSON.parse(data)})
  assert.equal(snapshot.summary.remaining,2)
})

test('401 清理失效会话，429 保留账号并提供可判断的错误码',async()=>{
  for(const [status,code] of [[401,'SESSION_INVALID'],[429,'QUOTA_BUSY']]){
    storage.set('saveany_token','session')
    global.fetch=async()=>Response.json({success:false,code,error:'请重试',quota:{summary:{remaining:0}}},{status})
    await assert.rejects(()=>summarizeVideo('https://example.com/video'),error=>error.status===status&&error.code===code)
    assert.equal(storage.has('saveany_token'),status!==401)
  }
})

test('读取本人保存结果使用认证 GET；没有记录返回 null，不触发生成',async()=>{
  storage.set('saveany_token','saved-session')
  let calls=0
  global.fetch=async(path,options)=>{
    calls++
    assert.equal(new URL(path,'https://local.test').pathname,'/api/summarize/result')
    assert.equal(new URL(path,'https://local.test').searchParams.get('url'),'https://example.com/watch?v=1&part=2')
    assert.equal(options.headers.Authorization,'Bearer saved-session')
    assert.equal(options.method,'GET')
    return Response.json({success:true,data:calls===1?{summary:'已保存',mindmap:'# 主题',subtitle:{full_text:'字幕'}}:null})
  }
  assert.equal((await fetchSummaryResult('https://example.com/watch?v=1&part=2')).summary,'已保存')
  assert.equal(await fetchSummaryResult('https://example.com/watch?v=1&part=2'),null)
  assert.equal(calls,2)
})

test('恢复结果服务故障必须提示重试，不伪装成无记录',async()=>{
  global.fetch=async()=>Response.json({success:false,code:'ACCOUNT_SERVICE_UNAVAILABLE',error:'服务暂不可用'},{status:503})
  await assert.rejects(()=>fetchSummaryResult('https://example.com/video'),error=>error.status===503)
})
