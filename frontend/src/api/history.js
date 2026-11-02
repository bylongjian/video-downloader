import {authHeaders,getToken} from './auth.js'
import {invalidateSession} from '../composables/useAuth.js'

async function request(path,method='GET',body,signal) {
  const token=getToken()
  let response
  try {
    response=await fetch(`/api/history${path}`,{method,headers:{...authHeaders(),...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal})
  } catch(cause) {
    if(signal?.aborted)throw cause
    throw new Error('网络连接失败，请检查连接后重试。')
  }
  if(response.status===401)invalidateSession(token)
  let result
  try {result=await response.json()}
  catch {throw Object.assign(new Error(`历史服务响应异常（HTTP ${response.status}），请稍后重试。`),{status:response.status})}
  if(!response.ok)throw Object.assign(new Error(result.error||'历史操作失败，请重试。'),{status:response.status,code:result.code})
  return result.data
}

export function fetchHistory(kind='summary',cursor=null,signal) {
  const query=new URLSearchParams({kind,limit:'20'})
  if(cursor)query.set('cursor',cursor)
  return request('?'+query,'GET',undefined,signal)
}
export function fetchHistoryDetail(kind,id,signal) {return request(`/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,'GET',undefined,signal)}
export function deleteHistory(kind,id,signal) {return request(`/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,'DELETE',undefined,signal)}
export function createDownloadHistory(payload,signal) {return request('/downloads','POST',payload,signal)}
export function recordDownloadEvent(id,event,signal) {return request(`/downloads/${encodeURIComponent(id)}/events`,'POST',{event},signal)}
