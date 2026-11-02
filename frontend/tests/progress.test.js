import {test} from 'node:test'
import assert from 'node:assert/strict'
import {effectScope} from 'vue'

const storage=new Map()
global.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}
global.window=new EventTarget()
window.location=new URL('https://saveany.example/queue')
const auth=await import('../src/api/auth.js')
const {useAuth}=await import('../src/composables/useAuth.js')
const {useTaskProgress}=await import('../src/composables/useTaskProgress.js')

class Socket {
  static instances=[]
  constructor(url){this.url=url;this.readyState=0;this.sent=[];Socket.instances.push(this)}
  open(){this.readyState=1;this.onopen?.()}
  send(value){this.sent.push(JSON.parse(value))}
  frame(value){this.onmessage?.({data:JSON.stringify(value)})}
  close(){this.readyState=3;this.onclose?.({code:1000})}
}
function setup(t){
  Socket.instances=[];global.WebSocket=Socket;auth.setToken('progress-session')
  const timers=new Map();let next=0
  const originalSet=global.setTimeout,originalClear=global.clearTimeout
  global.setTimeout=(fn,delay)=>{const id=++next;timers.set(id,{fn,delay});return id}
  global.clearTimeout=id=>timers.delete(id)
  const scope=effectScope(),progress=scope.run(()=>useTaskProgress())
  t.after(()=>{scope.stop();global.setTimeout=originalSet;global.clearTimeout=originalClear})
  return {scope,progress,timers,async run(delay){const entry=[...timers].find(([,timer])=>timer.delay===delay);assert.ok(entry,`missing ${delay}ms timer`);timers.delete(entry[0]);await entry[1].fn()}}
}
function snapshot(version,state='running',progress={downloaded_bytes:1024,total_bytes:null,percent:null}){
  return {id:'task/one',kind:'download',version,state,stage:'downloading',progress}
}

test('首帧认证、递增版本和未知总量保持真实，终态立即停止订阅',t=>{
  const {progress,timers}=setup(t),seen=[]
  progress.connectTaskProgress('task/one',task=>seen.push(task))
  const socket=Socket.instances[0],url=new URL(socket.url)
  assert.equal(url.protocol,'wss:');assert.equal(url.pathname,'/api/tasks/task%2Fone/progress');assert.equal(url.search,'')
  socket.open();assert.deepEqual(socket.sent,[{type:'authenticate',token:'progress-session'}])
  assert.equal(progress.isTaskConnected('task/one'),false)
  socket.frame({type:'snapshot',task:snapshot(3)})
  socket.frame({type:'update',task:snapshot(2)})
  socket.frame({type:'update',task:snapshot(3)})
  socket.frame({type:'heartbeat'})
  assert.equal(progress.isTaskConnected('task/one'),true)
  assert.equal(seen.length,1);assert.equal(seen[0].progress.percent,null);assert.equal(seen[0].progress.total_bytes,null)
  socket.frame({type:'update',task:{...snapshot(4,'succeeded'),stage:'ready'}})
  assert.deepEqual(seen.map(task=>task.version),[3,4]);assert.equal(socket.readyState,3)
  assert.equal(progress.isTaskConnected('task/one'),false);assert.equal(timers.size,0)
})

test('断线按 1/2/4/8/15 秒重查快照后重连，心跳超时与HTTP终态正确收尾',async t=>{
  const {progress,timers,run}=setup(t),seen=[],order=[]
  let version=0,terminal=false
  global.fetch=async(path,options)=>{order.push('get');assert.equal(path,'/api/tasks/task%2Fone');assert.equal(options.headers.Authorization,'Bearer progress-session');return Response.json({data:snapshot(++version,terminal?'succeeded':'running')})}
  progress.connectTaskProgress('task/one',task=>seen.push(task))
  for(const delay of [1000,2000,4000,8000,15000,15000]){
    Socket.instances.at(-1).close()
    assert.equal(progress.isTaskConnected('task/one'),false)
    const count=Socket.instances.length
    await run(delay)
    assert.equal(order.length,count);assert.equal(Socket.instances.length,count+1)
  }
  const socket=Socket.instances.at(-1);socket.open();socket.frame({type:'snapshot',task:snapshot(++version)})
  await run(40000)
  assert.equal(socket.readyState,3);assert.ok([...timers.values()].some(timer=>timer.delay===1000))
  terminal=true;const count=Socket.instances.length
  await run(1000)
  assert.equal(seen.at(-1).state,'succeeded');assert.equal(Socket.instances.length,count);assert.equal(timers.size,0)
})

test('换号和作用域卸载取消连接与重查，旧账号迟到响应不能写入新会话',async t=>{
  const {scope,progress,timers,run}=setup(t),seen=[]
  let finish,signal
  global.fetch=async(path,options)=>{signal=options.signal;return new Promise(resolve=>finish=resolve)}
  progress.connectTaskProgress('task/one',task=>seen.push(task))
  const oldSocket=Socket.instances[0];oldSocket.open()
  const lateMessage=oldSocket.onmessage
  oldSocket.close();const pending=run(1000)
  auth.setToken('new-account')
  global.fetch=async()=>Response.json({data:{user:{id:'new-user'},quota:null}})
  await useAuth().restoreSession()
  assert.equal(signal.aborted,true)
  finish(Response.json({data:snapshot(99)}));await pending
  lateMessage({data:JSON.stringify({type:'update',task:snapshot(100)})})
  assert.equal(seen.length,0);assert.equal(Socket.instances.length,1);assert.equal(timers.size,0)
  progress.connectTaskProgress('task/one',task=>seen.push(task))
  const currentSocket=Socket.instances.at(-1);currentSocket.open()
  scope.stop();currentSocket.frame({type:'update',task:snapshot(101)})
  assert.equal(currentSocket.readyState,3);assert.equal(timers.size,0);assert.equal(seen.length,0)
})
