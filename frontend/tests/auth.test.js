import { test } from 'node:test'
import assert from 'node:assert/strict'

const storage = new Map()
global.localStorage = {getItem:key=>storage.get(key) ?? null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}
global.window = new EventTarget()
const api = await import('../src/api/auth.js')
const {useAuth} = await import('../src/composables/useAuth.js')
const {updateQuota}=await import('../src/composables/useAuth.js')
const auth = useAuth()
const user = {id:1,email:'reader@example.com'}

test('注册、恢复身份与服务端退出使用本服务 JSON 和 Bearer',async()=>{
  storage.clear()
  const calls=[]
  global.fetch=async(path,options)=>{
    calls.push([path,options])
    return Response.json({success:true,data:path.endsWith('logout')?{logged_out:true}:path.endsWith('me')?{user}:{token:'session-one',token_type:'bearer',user}})
  }
  await auth.register(user.email,'password123')
  assert.equal(api.getToken(),'session-one')
  assert.deepEqual(JSON.parse(calls[0][1].body),{email:user.email,password:'password123'})
  await auth.restoreSession()
  assert.deepEqual(auth.user.value,user)
  assert.equal(calls[1][1].headers.Authorization,'Bearer session-one')
  await auth.logout()
  assert.equal(calls[2][0],'/api/auth/logout')
  assert.equal(api.getToken(),null)
  assert.equal(auth.user.value,null)
})

test('身份过期清理 Token，网络故障保留 Token 并可重试',async()=>{
  api.setToken('expired')
  global.fetch=async()=>Response.json({success:false,code:'SESSION_INVALID',error:'登录已失效'},{status:401})
  await auth.restoreSession()
  assert.equal(api.getToken(),null)
  api.setToken('valid-but-offline')
  global.fetch=async()=>{throw new TypeError('fetch failed')}
  await auth.restoreSession()
  assert.equal(api.getToken(),'valid-but-offline')
  assert.match(auth.authError.value,/网络|连接/)
  assert.equal(auth.authReady.value,true)
})

test('跨标签页换号使旧身份响应失效；退出网络失败不伪装为成功',async()=>{
  let finish
  api.setToken('old')
  global.fetch=()=>new Promise(resolve=>finish=resolve)
  const pending=auth.restoreSession()
  const version=auth.sessionVersion.value
  api.setToken('new')
  global.fetch=async()=>Response.json({success:true,data:{user:{id:2,email:'new@example.com'}}})
  const event=new Event('storage');event.key='saveany_token'
  window.dispatchEvent(event)
  await new Promise(resolve=>setTimeout(resolve,0))
  finish(Response.json({success:true,data:{user}}))
  await pending
  assert.equal(auth.user.value.id,2)
  assert.ok(auth.sessionVersion.value>version)
  global.fetch=async()=>{throw new TypeError('fetch failed')}
  await assert.rejects(()=>auth.logout(),/网络|连接/)
  assert.equal(api.getToken(),'new')
  assert.equal(auth.user.value.id,2)
})

test('同账号的迟到额度响应和网络错误不覆盖最新结果',async()=>{
  api.setToken('quota-session')
  global.fetch=async()=>Response.json({success:true,data:{user,quota:{summary:{remaining:3}}}})
  await auth.restoreSession()
  let finish,fail
  global.fetch=()=>new Promise(resolve=>finish=resolve)
  const old=auth.refreshQuota()
  updateQuota({summary:{remaining:1}},auth.sessionVersion.value)
  finish(Response.json({success:true,data:{summary:{remaining:0}}}))
  await old
  assert.equal(auth.quota.value.summary.remaining,1)
  global.fetch=()=>new Promise((resolve,reject)=>fail=reject)
  const offline=auth.refreshQuota()
  global.fetch=async()=>Response.json({success:true,data:{summary:{remaining:2}}})
  await auth.refreshQuota()
  fail(new TypeError('fetch failed'))
  await offline
  assert.equal(auth.quota.value.summary.remaining,2)
  assert.equal(auth.authError.value,'')
})

test('关闭弹窗取消旧登录，新一次登录不会被旧响应抢先替换',async()=>{
  storage.clear();await auth.restoreSession()
  const responses=[]
  global.fetch=()=>new Promise(resolve=>responses.push(resolve))
  const controller=new AbortController()
  const old=auth.login('old@example.com','password123',controller.signal)
  controller.abort()
  const latest=auth.login('new@example.com','password123')
  responses[0](Response.json({success:true,data:{token:'old-token',user:{id:1,email:'old@example.com'}}}))
  assert.equal(await old,false)
  assert.equal(api.getToken(),null)
  responses[1](Response.json({success:true,data:{token:'new-token',user:{id:2,email:'new@example.com'}}}))
  assert.equal(await latest,true)
  assert.equal(auth.user.value.email,'new@example.com')
})
