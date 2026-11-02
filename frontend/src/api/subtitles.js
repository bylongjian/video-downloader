import {authHeaders,getToken} from './auth.js'
import {invalidateSession} from '../composables/useAuth.js'

async function request(path,method='GET',body,signal) {
  const token=getToken()
  let response
  try {
    response=await fetch(`/api${path}`,{method,headers:{...authHeaders(),...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal})
  } catch(cause) {
    if(signal?.aborted)throw cause
    throw new Error('网络连接失败，请检查连接后重试。')
  }
  if(response.status===401)invalidateSession(token)
  let result
  try {result=await response.json()}
  catch {throw Object.assign(new Error(`字幕服务响应异常（HTTP ${response.status}），请稍后重试。`),{status:response.status})}
  if(!response.ok)throw Object.assign(new Error(result.error||result.detail||'字幕操作失败，请重试。'),{status:response.status,code:result.code,quota:result.quota})
  return result.data
}

export function translateSubtitles(payload,signal){return request('/subtitles/translate','POST',payload,signal)}
export function fetchTranslation(sourceKind,sourceId,target,signal){return request('/subtitles/translation?'+new URLSearchParams({source_kind:sourceKind,source_id:sourceId,target_language:target}),'GET',undefined,signal)}
export function deleteTranslation(id,signal){return request(`/subtitles/translations/${encodeURIComponent(id)}`,'DELETE',undefined,signal)}
export function createTranscription(payload,signal){return request('/transcriptions','POST',payload,signal)}
export function fetchTranscription(url,signal){return request('/transcriptions/result?'+new URLSearchParams({url}),'GET',undefined,signal)}
export function deleteTranscription(id,signal){return request(`/transcriptions/${encodeURIComponent(id)}`,'DELETE',undefined,signal)}
