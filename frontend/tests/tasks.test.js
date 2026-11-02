import {test} from 'node:test'
import assert from 'node:assert/strict'

const storage=new Map()
global.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}
global.window=new EventTarget()
const auth=await import('../src/api/auth.js')
const api=await import('../src/api/tasks.js')

test('批次提交、分页、单项取消与重试都使用本人 Bearer 和真实任务接口',async()=>{
  assert.equal(typeof api.createBatch,'function')
  auth.setToken('queue-session')
  const calls=[],controller=new AbortController()
  global.fetch=async(path,options)=>{calls.push([path,options]);return Response.json({success:true,data:{items:[],next_cursor:null}})}
  const input={request_key:'batch-one',items:[{url:'https://example.com/video',format_id:'bestvideo+bestaudio/best'}]}
  await api.createBatch(input,controller.signal)
  await api.fetchTasks({kind:'download',cursor:'next+/='},controller.signal)
  await api.fetchBatch('batch/id',controller.signal)
  await api.fetchTask('task/id',controller.signal)
  await api.cancelTask('task/id',controller.signal)
  await api.retryTask('task/id','retry-one',controller.signal)
  await api.fetchTaskResult('task/id',controller.signal)
  assert.deepEqual(JSON.parse(calls[0][1].body),input)
  const address=new URL(calls[1][0],'http://localhost')
  assert.equal(address.pathname,'/api/tasks')
  assert.equal(address.searchParams.get('cursor'),'next+/=')
  assert.equal(address.searchParams.get('kind'),'download')
  assert.equal(address.searchParams.get('limit'),'20')
  assert.deepEqual(calls.slice(2).map(([path,options])=>[path,options.method]),[
    ['/api/batches/batch%2Fid','GET'],['/api/tasks/task%2Fid','GET'],['/api/tasks/task%2Fid/cancel','POST'],['/api/tasks/task%2Fid/retry','POST'],['/api/tasks/task%2Fid/result','GET'],
  ])
  assert.deepEqual(JSON.parse(calls[5][1].body),{request_key:'retry-one'})
  for(const [path,options] of calls){assert.ok(path.startsWith('/api/'));assert.equal(options.headers.Authorization,'Bearer queue-session');assert.equal(options.signal,controller.signal)}
})

test('任务 401 清理过期会话，旧账号延迟响应不会退出新账号',async()=>{
  assert.equal(typeof api.fetchTasks,'function')
  auth.setToken('expired')
  global.fetch=async()=>Response.json({success:false,code:'SESSION_INVALID',error:'请重新登录'},{status:401})
  await assert.rejects(()=>api.fetchTasks(),cause=>cause.status===401&&cause.code==='SESSION_INVALID')
  assert.equal(auth.getToken(),null)
  auth.setToken('old-account')
  let finish
  global.fetch=()=>new Promise(resolve=>finish=resolve)
  const pending=api.fetchTasks()
  auth.setToken('new-account')
  finish(Response.json({success:false,code:'SESSION_INVALID',error:'已过期'},{status:401}))
  await assert.rejects(()=>pending,cause=>cause.status===401)
  assert.equal(auth.getToken(),'new-account')
})

test('文件以鉴权 Blob 领取并保留文件名，过期和网络错误不伪装成成功',async()=>{
  assert.equal(typeof api.downloadTaskFile,'function')
  auth.setToken('file-session')
  global.fetch=async(path,options)=>{
    assert.equal(path,'/api/tasks/file%2Fid/file')
    assert.equal(options.headers.Authorization,'Bearer file-session')
    return new Response('media',{headers:{'Content-Disposition':"attachment; filename*=UTF-8''%E8%A7%86%E9%A2%91.mp4"}})
  }
  const file=await api.downloadTaskFile('file/id')
  assert.equal(await file.blob.text(),'media');assert.equal(file.filename,'视频.mp4')
  global.fetch=async()=>Response.json({success:false,code:'FILE_EXPIRED',error:'文件已过期，请重新下载'},{status:410})
  await assert.rejects(()=>api.downloadTaskFile('one'),cause=>cause.status===410&&cause.code==='FILE_EXPIRED')
  global.fetch=async()=>{throw new TypeError('fetch failed')}
  await assert.rejects(()=>api.fetchTasks(),/网络|连接/)
  const controller=new AbortController();controller.abort()
  global.fetch=async()=>{throw new DOMException('Aborted','AbortError')}
  await assert.rejects(()=>api.fetchTasks({},controller.signal),cause=>cause.name==='AbortError')
  global.fetch=async()=>new Response('<html>offline</html>',{status:503})
  await assert.rejects(()=>api.fetchTasks(),/503/)
})
