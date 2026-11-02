import { test } from 'node:test'
import assert from 'node:assert/strict'

const storage=new Map()
global.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}
global.window=new EventTarget()
const auth=await import('../src/api/auth.js')
const api=await import('../src/api/history.js')

test('本人历史的分页、详情、删除与下载事件只请求本服务并携带 Bearer',async()=>{
  auth.setToken('history-session')
  const calls=[],controller=new AbortController()
  global.fetch=async(path,options)=>{
    calls.push([path,options])
    return Response.json({success:true,data:{items:[],next_cursor:null,id:'download-one',deleted:true}})
  }
  assert.deepEqual((await api.fetchHistory('summary','next+/=',controller.signal)).items,[])
  await api.fetchHistoryDetail('summary','result/id',controller.signal)
  await api.deleteHistory('summary','result/id',controller.signal)
  const input={url:'https://video.example/watch?v=one',video_title:'示例',platform:'YouTube',format_id:'137'}
  await api.createDownloadHistory(input,controller.signal)
  await api.recordDownloadEvent('download/id','browser_handoff',controller.signal)
  const address=new URL(calls[0][0],'http://localhost')
  assert.equal(address.pathname,'/api/history')
  assert.equal(address.searchParams.get('kind'),'summary')
  assert.equal(address.searchParams.get('limit'),'20')
  assert.equal(address.searchParams.get('cursor'),'next+/=')
  assert.deepEqual(calls.slice(1).map(([path,options])=>[path,options.method]),[
    ['/api/history/summary/result%2Fid','GET'],['/api/history/summary/result%2Fid','DELETE'],
    ['/api/history/downloads','POST'],['/api/history/downloads/download%2Fid/events','POST'],
  ])
  assert.deepEqual(JSON.parse(calls[3][1].body),input)
  assert.deepEqual(JSON.parse(calls[4][1].body),{event:'browser_handoff'})
  for(const [path,options] of calls){assert.ok(path.startsWith('/api/history'));assert.equal(options.headers.Authorization,'Bearer history-session');assert.equal(options.signal,controller.signal)}
})

test('401 清理当前会话，旧账号迟到的 401 不清理新账号',async()=>{
  auth.setToken('expired')
  global.fetch=async()=>Response.json({success:false,code:'SESSION_INVALID',error:'请重新登录'},{status:401})
  await assert.rejects(()=>api.fetchHistory('download'),cause=>cause.status===401&&cause.code==='SESSION_INVALID')
  assert.equal(auth.getToken(),null)
  auth.setToken('old-account')
  let finish
  global.fetch=()=>new Promise(resolve=>finish=resolve)
  const pending=api.fetchHistory('summary')
  auth.setToken('new-account')
  finish(Response.json({success:false,code:'SESSION_INVALID',error:'旧登录过期'},{status:401}))
  await assert.rejects(()=>pending,cause=>cause.status===401)
  assert.equal(auth.getToken(),'new-account')
})

test('网络故障保留账号并提示重试，取消保留 AbortError，非 JSON 错误不伪装为空历史',async()=>{
  auth.setToken('still-valid')
  global.fetch=async()=>{throw new TypeError('fetch failed')}
  await assert.rejects(()=>api.fetchHistory('summary'),/网络|连接/)
  assert.equal(auth.getToken(),'still-valid')
  const controller=new AbortController();controller.abort()
  global.fetch=async()=>{throw new DOMException('Aborted','AbortError')}
  await assert.rejects(()=>api.fetchHistory('summary',null,controller.signal),cause=>cause.name==='AbortError')
  global.fetch=async()=>new Response('<html>offline</html>',{status:503})
  await assert.rejects(()=>api.fetchHistoryDetail('summary','one'),/503/)
})
