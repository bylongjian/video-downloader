import {getCurrentScope,onScopeDispose,shallowRef,watch} from 'vue'
import {getToken} from '../api/auth.js'
import {fetchTask} from '../api/tasks.js'
import {useAuth} from './useAuth.js'

const terminalStates=new Set(['succeeded','failed','cancelled','interrupted'])
const retryDelays=[1000,2000,4000,8000,15000]

export function useTaskProgress(){
  const {sessionVersion}=useAuth(),subscriptions=new Map(),connected=shallowRef({})
  function current(entry){return subscriptions.get(entry.id)===entry&&entry.session===sessionVersion.value&&entry.token===getToken()}
  function setConnected(id,value){
    const next={...connected.value}
    if(value)next[id]=true
    else delete next[id]
    connected.value=next
  }
  function closeTaskProgress(taskId){
    const entries=taskId===undefined?[...subscriptions.values()]:[subscriptions.get(taskId)]
    for(const entry of entries){
      if(!entry)continue
      subscriptions.delete(entry.id);setConnected(entry.id,false)
      clearTimeout(entry.retryTimer);clearTimeout(entry.heartbeatTimer);entry.controller?.abort()
      if(entry.socket){
        entry.socket.onopen=entry.socket.onmessage=entry.socket.onclose=entry.socket.onerror=null
        if(entry.socket.readyState<2)entry.socket.close()
      }
    }
  }
  function accept(entry,task){
    // 服务端消息是输入边界；拒绝错误任务和无版本快照。
    if(!task||task.id!==entry.id||!Number.isInteger(task.version)||typeof task.state!=='string')return false
    if(task.version>entry.version){entry.version=task.version;entry.onSnapshot(task)}
    if(terminalStates.has(task.state)&&task.version>=entry.version)closeTaskProgress(entry.id)
    return true
  }
  function scheduleRetry(entry){
    if(!current(entry)||entry.retryTimer)return
    setConnected(entry.id,false);clearTimeout(entry.heartbeatTimer)
    const delay=retryDelays[Math.min(entry.attempt++,retryDelays.length-1)]
    entry.retryTimer=setTimeout(async()=>{
      entry.retryTimer=null
      if(!current(entry))return
      const control=new AbortController();entry.controller=control
      try{
        const task=await fetchTask(entry.id,control.signal)
        if(!current(entry))return
        if(!accept(entry,task)){scheduleRetry(entry);return}
        if(current(entry))open(entry)
      }catch(cause){
        if(!current(entry))return
        if(cause.status===403||cause.status===404)closeTaskProgress(entry.id)
        else scheduleRetry(entry)
      }finally{if(entry.controller===control)entry.controller=null}
    },delay)
  }
  function armHeartbeat(entry,socket){
    clearTimeout(entry.heartbeatTimer)
    // 服务端每15秒发心跳，留出网络抖动时间；静默连接也必须恢复轮询。
    entry.heartbeatTimer=setTimeout(()=>{
      if(!current(entry)||entry.socket!==socket)return
      socket.onopen=socket.onmessage=socket.onclose=socket.onerror=null
      if(socket.readyState<2)socket.close()
      scheduleRetry(entry)
    },40000)
  }
  function open(entry){
    if(!current(entry))return
    const url=new URL(`/api/tasks/${encodeURIComponent(entry.id)}/progress`,window.location.href)
    url.protocol=url.protocol==='https:'?'wss:':'ws:'
    let socket
    try{socket=new WebSocket(url.href)}catch{scheduleRetry(entry);return}
    entry.socket=socket;armHeartbeat(entry,socket)
    socket.onopen=()=>{
      if(current(entry)&&entry.socket===socket)socket.send(JSON.stringify({type:'authenticate',token:entry.token}))
    }
    socket.onmessage=event=>{
      if(!current(entry)||entry.socket!==socket)return
      let frame
      try{frame=JSON.parse(event.data)}catch{return}
      if(!frame||typeof frame!=='object')return
      if(frame.type==='heartbeat'){armHeartbeat(entry,socket);return}
      if(!['snapshot','update'].includes(frame.type)||!accept(entry,frame.task)||!current(entry))return
      entry.attempt=0;setConnected(entry.id,true);armHeartbeat(entry,socket)
    }
    socket.onclose=()=>{if(current(entry)&&entry.socket===socket)scheduleRetry(entry)}
    socket.onerror=()=>{
      if(!current(entry)||entry.socket!==socket)return
      if(socket.readyState<2)socket.close()
      scheduleRetry(entry)
    }
  }
  function connectTaskProgress(taskId,onSnapshot){
    if(subscriptions.has(taskId)){subscriptions.get(taskId).onSnapshot=onSnapshot;return}
    const token=getToken()
    if(!token)return
    const entry={id:taskId,onSnapshot,token,session:sessionVersion.value,version:-1,attempt:0,socket:null,retryTimer:null,heartbeatTimer:null,controller:null}
    subscriptions.set(taskId,entry);open(entry)
  }
  const stopSessionWatch=watch(sessionVersion,()=>closeTaskProgress(),{flush:'sync'})
  if(getCurrentScope())onScopeDispose(()=>{stopSessionWatch();closeTaskProgress()})
  return {connectTaskProgress,closeTaskProgress,isTaskConnected:taskId=>connected.value[taskId]===true}
}
