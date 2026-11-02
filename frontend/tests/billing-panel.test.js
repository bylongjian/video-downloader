import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {resolve,dirname} from 'node:path'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,h,nextTick} from 'vue'

const storage=new Map()
global.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}
global.window=new EventTarget()
const auth=await import('../src/api/auth.js')
const {useAuth}=await import('../src/composables/useAuth.js')
const file=resolve(import.meta.dirname,'../src/components/BillingPanel.vue')
const {descriptor}=parse(readFileSync(file,'utf8'))
const compiled=compileScript(descriptor,{id:'billing-panel-test',inlineTemplate:true}).content
const code=compiled.replace(/from\s+(['"])([^'"]+)\1/g,(_,quote,spec)=>'from '+JSON.stringify(spec.startsWith('.')?pathToFileURL(resolve(dirname(file),spec)).href:import.meta.resolve(spec)))
const BillingPanel=(await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))).default

function node(type,text=''){return {type,text,children:[],parent:null,props:{}}}
const renderer=createRenderer({
  createElement:node,createText:text=>node('text',text),createComment:text=>node('comment',text),
  setText:(node,text)=>node.text=text,setElementText:(node,text)=>node.text=text,patchProp:(node,key,oldValue,value)=>node.props[key]=value,
  insert(node,parent,anchor){if(node.parent)node.parent.children=node.parent.children.filter(child=>child!==node);node.parent=parent;parent.children.splice(anchor?parent.children.indexOf(anchor):parent.children.length,0,node)},
  remove(node){if(node.parent)node.parent.children=node.parent.children.filter(child=>child!==node)},
  parentNode:node=>node.parent,nextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]??null,
})
const text=node=>node.text+node.children.map(text).join(' ')
const find=(node,predicate)=>predicate(node)?node:node.children.map(child=>find(child,predicate)).find(Boolean)
const flush=async()=>{await new Promise(resolve=>setImmediate(resolve));await nextTick()}
const failure=(code,status=503)=>Response.json({success:false,error:'测试支付失败：'+code,code},{status})

async function mountPanel(checkout){
  const calls=[],redirects=[]
  window.location={search:'',assign:url=>redirects.push(url)}
  global.fetch=async(path,options)=>{
    if(path==='/api/auth/me')return Response.json({data:{user:{id:auth.getToken()},quota:{}}})
    if(path==='/api/billing/plans')return Response.json({data:{available:true,payment_mode:'test',plans:[{code:'vip_30d',amount_minor:990,currency:'cny',duration_days:30,limits:{summary:30,chat:100,translation:20,transcription:14400},transcription_single_limit:1800}]}})
    if(path.startsWith('/api/billing/orders?'))return Response.json({data:{items:[],next_cursor:null}})
    assert.equal(path,'/api/billing/checkout','测试不允许调用外部支付地址')
    calls.push({body:JSON.parse(options.body),signal:options.signal})
    return checkout(calls.length)
  }
  auth.setToken(crypto.randomUUID());await useAuth().restoreSession()
  const root=node('root'),app=renderer.createApp(BillingPanel)
  app.component('Icon',{render:()=>h('span')});app.mount(root);await flush()
  return {calls,redirects,content:()=>text(root),click:()=>find(find(root,node=>node.props.class==='billing-plan'),node=>node.type==='button').props.onClick(),unmount:()=>app.unmount()}
}

test('确定拒绝、价格不符和失效订单后，下一次主动购买创建新请求',async()=>{
  for(const code of ['PAYMENT_PROVIDER_REJECTED','PAYMENT_PRICE_MISMATCH','ORDER_NOT_PAYABLE']){
    const panel=await mountPanel(()=>failure(code,code==='ORDER_NOT_PAYABLE'?409:503))
    try {
      await panel.click();await flush()
      assert.equal(panel.calls.length,1,'失败后不能自动重新下单')
      const message=panel.content()
      await panel.click();await flush()
      assert.notEqual(panel.calls[1].body.request_key,panel.calls[0].body.request_key,code+' 必须允许重新购买')
      assert.match(message,/创建新的下单请求/)
      assert.deepEqual(panel.redirects,[])
    } finally {panel.unmount()}
  }
})

test('网络、支付商暂不可用和未知503均保留原请求，避免重复创建订单',async()=>{
  for(const code of ['network','PAYMENT_PROVIDER_UNAVAILABLE','UNKNOWN_ERROR']){
    const panel=await mountPanel(()=>{if(code==='network')throw new TypeError('fetch failed');return failure(code)})
    try {
      await panel.click();await flush()
      assert.equal(panel.calls.length,1)
      assert.match(panel.content(),/重试原下单请求/)
      await panel.click();await flush()
      assert.equal(panel.calls[1].body.request_key,panel.calls[0].body.request_key,code+' 不能仅因503就换请求标识')
      assert.ok(panel.calls[0].body.request_key)
      assert.deepEqual(panel.redirects,[])
    } finally {panel.unmount()}
  }
})

test('换号取消旧下单，迟到的确定失败不清除新账号请求，卸载后不跳转',async()=>{
  let finishOld,finishLast
  const panel=await mountPanel(attempt=>attempt===1?new Promise(resolve=>finishOld=resolve):attempt===4?new Promise(resolve=>finishLast=resolve):failure('PAYMENT_PROVIDER_UNAVAILABLE'))
  try {
    const old=panel.click()
    auth.setToken('new-account');await useAuth().restoreSession();await flush()
    assert.equal(panel.calls[0].signal.aborted,true)
    await panel.click();await flush()
    const newKey=panel.calls[1].body.request_key
    finishOld(failure('PAYMENT_PROVIDER_REJECTED'));await old;await flush()
    assert.doesNotMatch(panel.content(),/PAYMENT_PROVIDER_REJECTED/)
    await panel.click();await flush()
    assert.equal(panel.calls[2].body.request_key,newKey)
    assert.notEqual(newKey,panel.calls[0].body.request_key)
    const last=panel.click();panel.unmount()
    assert.equal(panel.calls[3].signal.aborted,true)
    finishLast(Response.json({data:{order_id:'late',checkout_url:'https://checkout.stripe.com/test_mock',payment_mode:'test'}}))
    await last
    assert.deepEqual(panel.redirects,[])
  } finally {panel.unmount()}
})
