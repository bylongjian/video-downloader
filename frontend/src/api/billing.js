import {authHeaders,getToken} from './auth.js'
import {invalidateSession} from '../composables/useAuth.js'

async function request(path,method='GET',body,signal) {
  const token=getToken()
  let response
  try {
    response=await fetch(`/api/billing${path}`,{method,headers:{...authHeaders(),...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal})
  } catch(cause) {
    if(signal?.aborted)throw cause
    throw new Error('网络连接失败，请检查连接后重试。')
  }
  if(response.status===401)invalidateSession(token)
  let result
  try {result=await response.json()}
  catch {throw Object.assign(new Error(`支付服务响应异常（HTTP ${response.status}），请稍后重试。`),{status:response.status})}
  if(!response.ok)throw Object.assign(new Error(result.error||'支付操作失败，请重试。'),{status:response.status,code:result.code})
  return result.data
}

export function fetchPlans(signal){return request('/plans','GET',undefined,signal)}
export function createCheckout(requestKey,planCode='vip_30d',signal){return request('/checkout','POST',{request_key:requestKey,plan_code:planCode},signal)}
export function fetchOrders(cursor=null,signal){const query=new URLSearchParams({limit:'20'});if(cursor)query.set('cursor',cursor);return request('/orders?'+query,'GET',undefined,signal)}
export function fetchOrder(id,signal){return request(`/orders/${encodeURIComponent(id)}`,'GET',undefined,signal)}
