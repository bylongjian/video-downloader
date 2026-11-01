import {ref,shallowRef,readonly} from 'vue'
import * as api from '../api/auth.js'

const user=shallowRef(null),quota=shallowRef(null),authReady=ref(false),authError=ref(''),sessionVersion=ref(0)
let activeToken=null,listening=false,quotaRequest=0,authenticationRequest=0

function clearIdentity(token=null) {
  sessionVersion.value++
  quotaRequest++
  activeToken=token;user.value=null;quota.value=null;authError.value=''
}

export function invalidateSession(token=api.getToken()) {
  if(token!==api.getToken())return
  api.removeToken();clearIdentity();authReady.value=true
}

async function restoreSession() {
  if(!listening){window.addEventListener('storage',event=>{if(event.key==='saveany_token'||event.key===null)restoreSession()});listening=true}
  const token=api.getToken()
  if(token!==activeToken)clearIdentity(token)
  const version=sessionVersion.value
  const request=++quotaRequest
  authReady.value=false;authError.value=''
  if(!token){authReady.value=true;return}
  try {
    const data=await api.fetchMe()
    if(version!==sessionVersion.value||token!==api.getToken())return
    user.value=data.user
    if(request===quotaRequest)quota.value=data.quota ?? null
  } catch(cause) {
    if(version!==sessionVersion.value||token!==api.getToken())return
    if(cause.status===401)invalidateSession(token)
    else authError.value=cause.message
  } finally {if(version===sessionVersion.value)authReady.value=true}
}

async function authenticate(action,email,password,signal) {
  const version=sessionVersion.value,token=api.getToken()
  const request=++authenticationRequest
  const data=await action(email,password,signal)
  if(signal?.aborted||request!==authenticationRequest||version!==sessionVersion.value||token!==api.getToken())return false
  api.setToken(data.token);clearIdentity(data.token)
  user.value=data.user;quota.value=data.quota ?? null;authReady.value=true
  return true
}

async function logout() {
  const token=api.getToken(),version=sessionVersion.value
  try {await api.logout()}
  catch(cause){if(cause.status!==401)throw cause}
  if(version===sessionVersion.value&&token===api.getToken())invalidateSession(token)
}

async function refreshQuota() {
  const version=sessionVersion.value,token=api.getToken()
  if(!token)return
  const request=++quotaRequest
  try {
    const snapshot=await api.fetchQuota()
    if(request===quotaRequest&&version===sessionVersion.value&&token===api.getToken()){quota.value=snapshot;authError.value=''}
  } catch(cause){
    if(request!==quotaRequest||version!==sessionVersion.value||token!==api.getToken())return
    if(cause.status===401)invalidateSession(token)
    else {quota.value=null;authError.value=cause.message}
  }
}

export function updateQuota(snapshot,version) {
  if(version===sessionVersion.value){quotaRequest++;quota.value=snapshot;authError.value=''}
}

export function useAuth() {
  return {user:readonly(user),quota:readonly(quota),authReady:readonly(authReady),authError:readonly(authError),sessionVersion:readonly(sessionVersion),restoreSession,refreshQuota,
    register:(email,password,signal)=>authenticate(api.register,email,password,signal),login:(email,password,signal)=>authenticate(api.login,email,password,signal),logout}
}
