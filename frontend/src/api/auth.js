const TOKEN_KEY='saveany_token'

export function getToken() {return localStorage.getItem(TOKEN_KEY)}
export function setToken(token) {localStorage.setItem(TOKEN_KEY,token)}
export function removeToken() {localStorage.removeItem(TOKEN_KEY)}
export function authHeaders() {const token=getToken();return token?{Authorization:`Bearer ${token}`}:{}}

async function request(path,method='GET',body,signal) {
  let response
  try {
    response=await fetch(`/api/auth/${path}`,{method,headers:{'Content-Type':'application/json',...authHeaders()},...(body===undefined?{}:{body:JSON.stringify(body)}),signal})
  } catch(cause){if(signal?.aborted)throw cause;throw new Error('网络连接失败，请检查连接后重试。')}
  let result
  try {result=await response.json()}
  catch {throw new Error(`账号服务响应异常（HTTP ${response.status}），请稍后重试。`)}
  if(!response.ok)throw Object.assign(new Error(result.error || '账号操作失败，请重试。'),{status:response.status,code:result.code})
  return result.data
}

export function register(email,password,signal) {return request('register','POST',{email,password},signal)}
export function login(email,password,signal) {return request('login','POST',{email,password},signal)}
export function fetchMe() {return request('me')}
export function logout() {return request('logout','POST')}
export function fetchQuota() {return request('quota')}
