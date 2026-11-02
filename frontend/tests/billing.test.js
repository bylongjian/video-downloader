import {test} from 'node:test'
import assert from 'node:assert/strict'

const storage=new Map()
global.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}
global.window=new EventTarget()
const auth=await import('../src/api/auth.js')
const api=await import('../src/api/billing.js').catch(()=>({}))

test('套餐与本人订单只读服务端，下单仅提交固定套餐和可复用request_key',async()=>{
  assert.equal(typeof api.fetchPlans,'function')
  auth.setToken('billing-session')
  const calls=[],controller=new AbortController()
  const plans={payment_mode:'test',available:false,plans:[{code:'vip_30d',amount_minor:990,currency:'cny',duration_days:30}]}
  global.fetch=async(path,options)=>{calls.push([path,options]);return Response.json({success:true,data:plans})}
  assert.deepEqual(await api.fetchPlans(controller.signal),plans)
  await api.fetchOrders('cursor+/=',controller.signal)
  await api.fetchOrder('order/id',controller.signal)
  await api.createCheckout('retry-same-key','vip_30d',controller.signal)
  await api.createCheckout('retry-same-key','vip_30d',controller.signal)
  assert.deepEqual(calls.map(([path,options])=>[path.split('?')[0],options.method]),[
    ['/api/billing/plans','GET'],['/api/billing/orders','GET'],['/api/billing/orders/order%2Fid','GET'],['/api/billing/checkout','POST'],['/api/billing/checkout','POST'],
  ])
  assert.equal(new URL(calls[1][0],'http://localhost').searchParams.get('cursor'),'cursor+/=')
  assert.equal(new URL(calls[1][0],'http://localhost').searchParams.get('limit'),'20')
  for(const [,options] of calls){assert.equal(options.headers.Authorization,'Bearer billing-session');assert.equal(options.signal,controller.signal)}
  for(const [,options] of calls.slice(3))assert.deepEqual(JSON.parse(options.body),{request_key:'retry-same-key',plan_code:'vip_30d'})
})

test('未配置和订单归属错误保留服务端提示；旧请求401不会清掉新账号',async()=>{
  assert.equal(typeof api.createCheckout,'function')
  auth.setToken('old-account')
  global.fetch=async()=>Response.json({success:false,error:'支付尚未配置完成，当前无法购买会员',code:'PAYMENT_NOT_CONFIGURED'},{status:503})
  await assert.rejects(()=>api.createCheckout('one'),cause=>cause.status===503&&cause.code==='PAYMENT_NOT_CONFIGURED'&&cause.message.includes('未配置'))
  global.fetch=async()=>Response.json({success:false,error:'订单不存在或无权访问',code:'ORDER_NOT_FOUND'},{status:404})
  await assert.rejects(()=>api.fetchOrder('other-account'),cause=>cause.status===404&&cause.code==='ORDER_NOT_FOUND')
  let finish
  global.fetch=()=>new Promise(resolve=>finish=resolve)
  const pending=api.fetchOrders()
  auth.setToken('new-account')
  finish(Response.json({success:false,error:'请登录',code:'SESSION_INVALID'},{status:401}))
  await assert.rejects(()=>pending,cause=>cause.status===401)
  assert.equal(auth.getToken(),'new-account')
})

test('支付网络错误和非JSON不冒充成功；主动取消保留AbortError',async()=>{
  assert.equal(typeof api.fetchOrder,'function')
  global.fetch=async()=>{throw new TypeError('fetch failed')}
  await assert.rejects(()=>api.createCheckout('same-key'),/网络|连接/)
  global.fetch=async()=>new Response('<html>offline</html>',{status:503})
  await assert.rejects(()=>api.fetchOrder('one'),/503/)
  const controller=new AbortController();controller.abort()
  global.fetch=async()=>{throw new DOMException('Aborted','AbortError')}
  await assert.rejects(()=>api.fetchPlans(controller.signal),cause=>cause.name==='AbortError')
})
