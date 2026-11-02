import {authHeaders,getToken} from './auth.js'
import {invalidateSession} from '../composables/useAuth.js'

async function request(path,method='GET',body,signal,file=false) {
  const token=getToken()
  let response
  try {
    response=await fetch(`/api${path}`,{method,headers:{...authHeaders(),...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal})
  } catch(cause) {
    if(signal?.aborted)throw cause
    throw new Error('网络连接失败，请检查连接后重试。')
  }
  if(response.status===401)invalidateSession(token)
  if(file&&response.ok) {
    const disposition=response.headers.get('Content-Disposition')||''
    const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i)
    const plain=disposition.match(/filename="([^"]+)"|filename=([^;]+)/i)
    let filename=plain?.[1]||plain?.[2]?.trim()||'video.mp4'
    if(encoded){try{filename=decodeURIComponent(encoded[1])}catch{/* 无效文件名使用普通名称 */}}
    return {blob:await response.blob(),filename:filename.replace(/[\\/\r\n]/g,'_')}
  }
  let result
  try {result=await response.json()}
  catch {throw Object.assign(new Error(`任务服务响应异常（HTTP ${response.status}），请稍后重试。`),{status:response.status})}
  if(!response.ok)throw Object.assign(new Error(result.error||'任务操作失败，请重试。'),{status:response.status,code:result.code})
  return result.data
}

export function createBatch(payload,signal){return request('/batches','POST',payload,signal)}
export function fetchBatch(id,signal){return request(`/batches/${encodeURIComponent(id)}`,'GET',undefined,signal)}
export function fetchTasks(filters={},signal){
  const query=new URLSearchParams({limit:'20'})
  for(const field of ['kind','state','batch_id','cursor'])if(filters[field])query.set(field,filters[field])
  return request(`/tasks?${query}`,'GET',undefined,signal)
}
export function fetchTask(id,signal){return request(`/tasks/${encodeURIComponent(id)}`,'GET',undefined,signal)}
export function fetchTaskResult(id,signal){return request(`/tasks/${encodeURIComponent(id)}/result`,'GET',undefined,signal)}
export function cancelTask(id,signal){return request(`/tasks/${encodeURIComponent(id)}/cancel`,'POST',undefined,signal)}
export function retryTask(id,requestKey,signal){return request(`/tasks/${encodeURIComponent(id)}/retry`,'POST',{request_key:requestKey},signal)}
export function downloadTaskFile(id,signal){return request(`/tasks/${encodeURIComponent(id)}/file`,'GET',undefined,signal,true)}
