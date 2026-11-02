<script setup>
import {computed,ref,watch,onUnmounted} from 'vue'
import {fetchPlans,createCheckout,fetchOrders,fetchOrder} from '../api/billing.js'
import {useAuth} from '../composables/useAuth.js'

const props=defineProps({orderId:{type:String,default:''}})
const emit=defineEmits(['close','login','entitlement-updated'])
const {user,authReady,authError,sessionVersion,restoreSession}=useAuth()
const query=new URLSearchParams(window.location.search)
const cancelledReturn=query.get('checkout')==='cancelled'||query.get('payment')==='cancel'
const plans=ref(null),plansLoading=ref(false),plansError=ref(''),checkoutError=ref(''),creating=ref(false)
const orders=ref([]),cursor=ref(null),ordersLoading=ref(false),ordersError=ref(''),ordersLoaded=ref(false)
const selectedId=ref(props.orderId||query.get('order_id')||''),order=ref(null),orderLoading=ref(false),orderError=ref('')
const plan=computed(()=>plans.value?.plans.find(item=>item.code==='vip_30d'))
const statusNames={creating:'正在创建',pending:'待确认付款',paid:'已确认付款',failed:'付款失败',expired:'订单已过期',refunded:'已全额退款',disputed:'付款争议',review_required:'部分退款，待人工处理'}
const terminalStates=new Set(['paid','failed','expired','refunded','disputed','review_required'])
const notifiedOrders=new Set()
let controller,checkoutController,timer,revision=0,orderRevision=0,checkoutRequestKey=''

function money(amount,currency){return new Intl.NumberFormat('zh-CN',{style:'currency',currency}).format(amount/100)}
function date(value){return new Date(value).toLocaleString('zh-CN',{hour12:false})}
function current(run,signal){return run===revision&&!signal.aborted}

async function loadPlans(run,signal){
  plansLoading.value=true;plansError.value=''
  try {const result=await fetchPlans(signal);if(current(run,signal))plans.value=result}
  catch(cause){if(current(run,signal))plansError.value=cause.message}
  finally {if(current(run,signal))plansLoading.value=false}
}

async function loadOrders(more=false){
  if(!authReady.value||!user.value||ordersLoading.value)return
  const run=revision,signal=controller.signal
  ordersLoading.value=true;ordersError.value=''
  try {
    const result=await fetchOrders(more?cursor.value:null,signal)
    if(!current(run,signal))return
    orders.value=more?[...orders.value,...result.items]:result.items;cursor.value=result.next_cursor;ordersLoaded.value=true
  } catch(cause){if(current(run,signal))ordersError.value=cause.message}
  finally {if(current(run,signal))ordersLoading.value=false}
}

async function checkOrder(){
  clearTimeout(timer)
  if(!selectedId.value||!authReady.value||!user.value||orderLoading.value)return
  const run=revision,selected=++orderRevision,id=selectedId.value,signal=controller.signal
  orderLoading.value=true;orderError.value=''
  try {
    const result=await fetchOrder(id,signal)
    if(!current(run,signal)||selected!==orderRevision)return
    order.value=result
    orders.value=orders.value.map(item=>item.id===result.id?result:item)
    if(result.status==='paid'&&!notifiedOrders.has(result.id)){
      notifiedOrders.add(result.id)
      emit('entitlement-updated',result.entitlement)
    }
    if(!terminalStates.has(result.status))timer=setTimeout(checkOrder,3000)
  } catch(cause){if(current(run,signal)&&selected===orderRevision)orderError.value=cause.message}
  finally {if(current(run,signal)&&selected===orderRevision)orderLoading.value=false}
}

function selectOrder(id){
  clearTimeout(timer);orderRevision++;selectedId.value=id;order.value=null;orderError.value='';orderLoading.value=false
  checkOrder()
}

async function checkout(){
  if(creating.value||!plans.value?.available||!plan.value)return
  if(!user.value){emit('login');return}
  checkoutController?.abort()
  const pending=new AbortController(),run=revision
  checkoutController=pending;creating.value=true;checkoutError.value=''
  checkoutRequestKey||=crypto.randomUUID()
  try {
    const result=await createCheckout(checkoutRequestKey,plan.value.code,pending.signal)
    if(!current(run,pending.signal))return
    // 只跳转到服务端创建并返回的支付会话，不从回跳参数或本地缓存授予权益。
    window.location.assign(result.checkout_url)
  } catch(cause){if(current(run,pending.signal))checkoutError.value=cause.message}
  finally {if(current(run,pending.signal))creating.value=false}
}

function restart(){
  revision++;orderRevision++;controller?.abort();checkoutController?.abort();clearTimeout(timer)
  controller=new AbortController()
  plans.value=null;plansError.value='';checkoutError.value='';creating.value=false
  orders.value=[];cursor.value=null;ordersError.value='';ordersLoaded.value=false;ordersLoading.value=false
  order.value=null;orderError.value='';orderLoading.value=false
  loadPlans(revision,controller.signal)
  loadOrders();checkOrder()
}

watch(sessionVersion,()=>{checkoutRequestKey='';notifiedOrders.clear()},{flush:'sync'})
watch(()=>props.orderId,id=>{selectedId.value=id||query.get('order_id')||''},{flush:'sync'})
watch([authReady,()=>user.value?.id,sessionVersion,()=>props.orderId],restart,{immediate:true,flush:'sync'})
onUnmounted(()=>{revision++;orderRevision++;controller?.abort();checkoutController?.abort();clearTimeout(timer)})
</script>

<template>
  <section class="card billing-card" aria-labelledby="billing-title">
    <div class="billing-heading"><div><p class="muted tiny">套餐与本人订单</p><h1 id="billing-title">会员与订单</h1></div><button class="button ghost" @click="emit('close')"><Icon name="arrow-left" />返回下载</button></div>
    <p v-if="plansLoading" role="status">正在读取服务端套餐…</p>
    <div v-if="plansError" class="billing-message inline-error" role="alert"><p>{{plansError}}</p><button class="text-button" @click="loadPlans(revision,controller.signal)">重试读取套餐</button></div>
    <template v-if="plans">
      <div class="billing-message" role="status"><strong>{{plans.payment_mode==='test'?'Stripe 测试环境':plans.payment_mode==='live'?'正式支付环境':'支付环境未就绪'}}</strong><p>{{plans.payment_mode==='test'?'当前是测试支付，测试权益仅在测试环境生效。':'付款状态与会员权益均以服务端确认结果为准。'}}</p><p v-if="!plans.available">支付尚未配置完成，当前无法购买会员。需管理员准备支付密钥、签名回调、一次性套餐 Price 和回跳地址。</p></div>
      <article v-if="plan" class="billing-plan"><h2>VIP · {{plan.duration_days}} 天</h2><p class="billing-price">{{money(plan.amount_minor,plan.currency)}}<span class="muted"> / 一次购买，不自动续费</span></p><p>每天 {{plan.limits.summary}} 次总结、{{plan.limits.chat}} 次问答、{{plan.limits.translation}} 次翻译、{{plan.limits.transcription/60}} 分钟转写；单条转写最多 {{plan.transcription_single_limit/60}} 分钟。</p><p class="muted">续购从当前有效到期时间起延长；到期恢复免费额度，当天用量不清零。</p><button class="button primary" :disabled="!plans.available||creating||!authReady" @click="checkout"><span v-if="creating" class="spinner"></span>{{creating?'正在创建支付会话…':!plans.available?'支付未配置':!user?'登录后购买':plans.payment_mode==='test'?'前往测试 Checkout':'前往 Checkout'}}</button><p v-if="checkoutError" class="inline-error" role="alert">{{checkoutError}} 点击购买会重试原下单请求。</p></article>
    </template>
    <div v-if="!authReady||!user" class="billing-message" role="status"><h2>{{!authReady?'正在恢复登录…':authError?'暂时无法确认登录状态':'登录后查看本人订单'}}</h2><p>{{authError||'订单只对所属账号可见，回跳地址不会直接开通会员。'}}</p><button v-if="authReady" class="button outline" @click="authError?restoreSession():emit('login')">{{authError?'重试恢复登录':'登录 / 注册'}}</button></div>
    <template v-else>
      <article v-if="selectedId" class="billing-order" aria-live="polite"><h2>付款确认</h2><p v-if="cancelledReturn" class="muted">你已从支付页面返回。返回或关闭页面不代表付款失败，正在向服务端确认订单。</p><p class="muted tiny billing-id">订单 {{selectedId}}</p><p v-if="orderLoading" role="status">正在查询订单…</p><div v-if="orderError" class="inline-error" role="alert"><p>{{orderError}}</p><button class="text-button" :disabled="orderLoading" @click="checkOrder">重试确认</button></div><template v-if="order"><strong>{{statusNames[order.status]||'状态待确认'}}</strong><p v-if="order.status==='paid'">服务端已确认付款。{{order.entitlement.tier==='vip'?'当前会员有效至 '+date(order.entitlement.expires_at)+'。':'当前会员已到期，请以账号当前权益为准。'}}</p><p v-else-if="!terminalStates.has(order.status)">付款尚未确认，页面每 3 秒自动查询。请勿重复付款；稍后也可以从订单列表继续查看。</p><p v-else-if="order.status==='review_required'">部分退款需管理员核对，当前权益以服务端为准。</p><p v-else-if="order.status==='refunded'||order.status==='disputed'">此订单的权益已撤销，其他有效订单的权益由服务端重新计算。</p><p v-else>此订单未开通会员，可重新选择套餐购买。</p><p class="muted tiny">{{order.payment_mode==='test'?'测试订单':'正式订单'}} · {{money(order.amount_minor,order.currency)}}</p><button class="text-button" :disabled="orderLoading" @click="checkOrder">刷新订单状态</button></template></article>
      <div class="billing-orders-heading"><h2>我的订单</h2><button class="text-button" :disabled="ordersLoading" @click="loadOrders()">刷新列表</button></div>
      <div v-if="ordersError" class="inline-error" role="alert"><p>{{ordersError}}</p><button class="text-button" :disabled="ordersLoading" @click="loadOrders(ordersLoaded)">重试加载</button></div>
      <p v-if="ordersLoading" role="status">正在读取订单…</p><p v-else-if="ordersLoaded&&!orders.length" class="muted">还没有订单，未配置支付时不会创建模拟订单。</p>
      <ul class="billing-orders"><li v-for="item in orders" :key="item.id"><div><strong>VIP · 30 天</strong><p class="muted tiny">{{item.payment_mode==='test'?'测试订单':'正式订单'}} · {{money(item.amount_minor,item.currency)}} · {{date(item.created_at)}}</p><p class="muted tiny billing-id">{{item.id}}</p></div><button class="button outline" @click="selectOrder(item.id)">{{statusNames[item.status]||'查看订单'}}</button></li></ul><button v-if="cursor" class="button outline" :disabled="ordersLoading" @click="loadOrders(true)">加载更多订单</button>
    </template>
  </section>
</template>

<style scoped>
.billing-card{padding:32px;min-width:0}.billing-heading,.billing-orders-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:24px}.billing-heading h1{font-size:30px;margin-top:6px}.billing-message,.billing-plan,.billing-order{padding:20px;border:1px solid var(--line);border-radius:14px;margin-bottom:24px}.billing-message{background:var(--surface)}.billing-card h2{font-size:21px}.billing-card p{line-height:1.7;margin-top:10px}.billing-card .button{margin-top:16px}.billing-heading>.button{margin-top:0}.billing-price{font-size:28px;font-weight:700}.billing-price span{font-size:15px;font-weight:400}.billing-orders{list-style:none;margin:0 0 20px;padding:0}.billing-orders li{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:20px 0;border-top:1px solid var(--line)}.billing-orders li>div{min-width:0}.billing-orders li>.button{margin:0;flex-shrink:0}.billing-id{overflow-wrap:anywhere}.billing-orders-heading{margin-bottom:10px}.billing-order strong{display:block;margin-top:12px}.billing-order>.text-button{margin-top:12px}
@media(max-width:640px){.billing-card{padding:22px 20px}.billing-heading{align-items:flex-start}.billing-heading h1{font-size:26px}.billing-heading>.button{padding-inline:10px}.billing-message,.billing-plan,.billing-order{padding:16px}.billing-orders li{align-items:flex-start;flex-direction:column;gap:12px}.billing-price span{display:block}}
</style>
