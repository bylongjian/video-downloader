<script setup>
import {computed,ref,watch,onUnmounted} from 'vue'
import {createBatch,fetchTasks,fetchTask,cancelTask,retryTask,downloadTaskFile} from '../api/tasks.js'
import {useAuth} from '../composables/useAuth.js'
import {useTaskProgress} from '../composables/useTaskProgress.js'

const emit=defineEmits(['close','login','open-result'])
const {user,authReady,authError,sessionVersion,restoreSession}=useAuth()
const {connectTaskProgress,closeTaskProgress,isTaskConnected}=useTaskProgress()
const urls=ref(['']),format=ref('bestvideo+bestaudio/best'),items=ref([]),cursor=ref(null)
const loading=ref(false),loaded=ref(false),submitting=ref(false),accepted=ref(false),submitError=ref(''),notice=ref(''),listError=ref('')
const actionErrors=ref({}),busy=ref({}),receiving=ref(null),handedOff=ref({})
const activeStates=new Set(['queued','running','cancelling'])
const retryStates=new Set(['failed','cancelled','interrupted'])
const statuses={queued:'等待处理',running:'正在处理',cancelling:'正在取消',succeeded:'处理完成',failed:'处理失败',cancelled:'已取消',interrupted:'服务重启，任务已中断'}
const stages={queued:'等待开始',extracting:'读取视频信息',downloading:'下载媒体文件',merging:'合并音视频',translating:'翻译字幕',transcribing:'识别语音',ready:'服务端已准备好'}
const kinds={download:'视频下载',translation:'字幕翻译',transcription:'语音转写'}
const activeCount=computed(()=>items.value.filter(task=>activeStates.has(task.state)).length)
let revision=0,timer,listController,batchKey='',retryKeys={},fileUrl=null,handoffTimer,handoffDone
const controllers=new Set(),subscriptions=new Set()
function controller(){const value=new AbortController();controllers.add(value);return value}
function current(request,version,signal){return request===revision&&version===sessionVersion.value&&!signal.aborted}
function editDraft(){batchKey='';accepted.value=false;notice.value='';submitError.value=''}
function addRow(){if(urls.value.length<10){urls.value.push('');editDraft()}}
function removeRow(index){urls.value.splice(index,1);editDraft()}
function mergeTask(snapshot){
  const index=items.value.findIndex(task=>task.id===snapshot.id)
  if(index===-1)items.value.unshift(snapshot)
  else if(snapshot.version>=items.value[index].version)items.value[index]=snapshot
}
function expiry(task){return task.file_expires_at&&new Date(task.file_expires_at).getTime()<=Date.now()}
function date(value){return new Date(value).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false})}
function bytes(value){
  const units=['B','KiB','MiB','GiB'],unit=Math.min(3,Math.floor(Math.log2(Math.max(value,1))/10))
  return `${(value/1024**unit).toFixed(unit?1:0)} ${units[unit]}`
}
function trackPercent(task){
  const progress=task.progress
  return progress?.total_bytes>0&&Number.isFinite(progress.percent)?Math.max(0,Math.min(100,progress.percent)):null
}
function syncSubscriptions(){
  const active=items.value.filter(task=>activeStates.has(task.state)),ids=new Set(active.map(task=>task.id))
  for(const id of subscriptions)if(!ids.has(id)){closeTaskProgress(id);subscriptions.delete(id)}
  if(!user.value)return
  for(const task of active)if(!subscriptions.has(task.id)){subscriptions.add(task.id);connectTaskProgress(task.id,mergeTask)}
}
function schedulePoll(){
  clearTimeout(timer)
  if(user.value&&activeCount.value)timer=setTimeout(poll,3000)
}
async function poll(){
  const request=revision,version=sessionVersion.value,control=controller()
  try {
    const tasks=items.value.filter(task=>activeStates.has(task.state)&&!isTaskConnected(task.id))
    const results=await Promise.allSettled(tasks.map(task=>fetchTask(task.id,control.signal)))
    if(!current(request,version,control.signal))return
    let error=''
    for(const result of results){if(result.status==='fulfilled')mergeTask(result.value);else error=result.reason.message}
    listError.value=error?'状态暂时无法更新：'+error:''
  } finally {controllers.delete(control);if(current(request,version,control.signal))schedulePoll()}
}
async function load(more=false){
  if(!authReady.value||!user.value||(more&&loading.value))return
  listController?.abort()
  const control=controller(),request=revision,version=sessionVersion.value
  listController=control;loading.value=true;listError.value=''
  try {
    const result=await fetchTasks(more?{cursor:cursor.value}:{},control.signal)
    if(!current(request,version,control.signal))return
    const existing=new Map(items.value.map(task=>[task.id,task]))
    if(!more)items.value=[]
    for(const task of result.items){
      const snapshot=existing.has(task.id)&&existing.get(task.id).version>task.version?existing.get(task.id):task
      const index=items.value.findIndex(item=>item.id===task.id)
      if(index===-1)items.value.push(snapshot)
      else if(snapshot.version>=items.value[index].version)items.value[index]=snapshot
    }
    cursor.value=result.next_cursor;loaded.value=true;schedulePoll()
  } catch(cause){if(current(request,version,control.signal))listError.value=cause.message}
  finally{controllers.delete(control);if(current(request,version,control.signal)&&listController===control)loading.value=false}
}
async function submit(){
  if(submitting.value||accepted.value)return
  const entries=urls.value.map(url=>url.trim()).filter(Boolean)
  if(!entries.length){submitError.value='请至少填写一个视频链接。';return}
  for(const url of entries){
    try{if(!['http:','https:'].includes(new URL(url).protocol))throw new Error()}
    catch{submitError.value='请使用完整的 http 或 https 视频链接。';return}
  }
  batchKey ||= crypto.randomUUID()
  const control=controller(),request=revision,version=sessionVersion.value
  submitting.value=true;submitError.value='';notice.value=''
  try{
    const result=await createBatch({request_key:batchKey,items:entries.map(url=>({url,format_id:format.value}))},control.signal)
    if(!current(request,version,control.signal))return
    accepted.value=true
    notice.value=`已接收 ${result.task_ids.length} 项任务${result.deduplicated_count?`，合并了 ${result.deduplicated_count} 个重复链接`:''}。可以离开页面，稍后回来领取文件。`
    await load()
  }catch(cause){if(current(request,version,control.signal))submitError.value=cause.message}
  finally{controllers.delete(control);if(current(request,version,control.signal))submitting.value=false}
}
async function act(task,action){
  if(busy.value[task.id])return
  const control=controller(),request=revision,version=sessionVersion.value
  busy.value[task.id]=action;actionErrors.value[task.id]=''
  try{
    if(action==='cancel'){
      await cancelTask(task.id,control.signal)
      if(!current(request,version,control.signal))return
      const snapshot=await fetchTask(task.id,control.signal)
      if(current(request,version,control.signal))mergeTask(snapshot)
    }else{
      retryKeys[task.id] ||= crypto.randomUUID()
      const snapshot=await retryTask(task.id,retryKeys[task.id],control.signal)
      if(current(request,version,control.signal)){mergeTask(snapshot);delete retryKeys[task.id];notice.value='已创建新的重试任务，原任务记录会保留。'}
    }
    if(current(request,version,control.signal))schedulePoll()
  }catch(cause){if(current(request,version,control.signal))actionErrors.value[task.id]=cause.message}
  finally{controllers.delete(control);if(current(request,version,control.signal))delete busy.value[task.id]}
}
async function receive(task){
  if(receiving.value)return
  const control=controller(),request=revision,version=sessionVersion.value
  receiving.value=task.id;actionErrors.value[task.id]=''
  try{
    const file=await downloadTaskFile(task.id,control.signal)
    if(!current(request,version,control.signal))return
    fileUrl=URL.createObjectURL(file.blob)
    const anchor=document.createElement('a');anchor.href=fileUrl;anchor.download=file.filename
    document.body.appendChild(anchor);anchor.click();anchor.remove()
    handedOff.value[task.id]=true
    // 给浏览器一秒接管 Blob，再释放地址；释放前不接收下一份文件。
    await new Promise(resolve=>{handoffDone=resolve;handoffTimer=setTimeout(resolve,1000)})
  }catch(cause){
    if(current(request,version,control.signal)){
      actionErrors.value[task.id]=cause.message
      if(cause.status===410)mergeTask({...task,file_available:false})
    }
  }finally{
    controllers.delete(control)
    if(current(request,version,control.signal)){if(fileUrl)URL.revokeObjectURL(fileUrl);fileUrl=null;handoffDone=null;clearTimeout(handoffTimer);receiving.value=null}
  }
}
function dispose(){
  revision++;clearTimeout(timer);for(const control of controllers)control.abort();controllers.clear()
  closeTaskProgress();subscriptions.clear()
  clearTimeout(handoffTimer);handoffDone?.();handoffDone=null
  if(fileUrl)URL.revokeObjectURL(fileUrl);fileUrl=null
}
function reset(){
  dispose();items.value=[];cursor.value=null;loading.value=false;loaded.value=false;submitting.value=false
  urls.value=[''];format.value='bestvideo+bestaudio/best';batchKey='';retryKeys={};accepted.value=false
  submitError.value='';listError.value='';notice.value='';actionErrors.value={};busy.value={};receiving.value=null;handedOff.value={}
  load()
}
watch([authReady,user,sessionVersion],reset,{immediate:true,flush:'sync'})
watch(()=>items.value.filter(task=>activeStates.has(task.state)).map(task=>task.id),syncSubscriptions)
onUnmounted(dispose)
</script>

<template>
  <section class="card queue-card" aria-labelledby="queue-title">
    <div class="queue-heading"><div><p class="muted tiny">登录即可免费使用 · 每批最多 10 项</p><h1 id="queue-title">批量下载与任务</h1></div><button class="button ghost" @click="emit('close')"><Icon name="arrow-left" />返回下载</button></div>
    <div v-if="!authReady||!user" class="empty-state" role="status"><Icon name="lock" /><h2>{{!authReady?'正在恢复登录…':authError?'暂时无法确认登录状态':'登录后使用批量下载'}}</h2><p>{{authError||'任务会保存在当前账号，离开页面后也能回来查看。'}}</p><button v-if="authReady" class="button primary" @click="authError?restoreSession():emit('login')">{{authError?'重试恢复登录':'登录 / 注册'}}</button></div>
    <template v-else>
      <form class="queue-form" @submit.prevent="submit">
        <div class="queue-section-heading"><h2>添加视频链接</h2><span class="muted tiny">{{urls.length}} / 10 行</span></div>
        <p class="muted queue-help">每行一个链接，统一使用下方格式。无需先解析，任务会逐项处理。</p>
        <div v-for="(_,index) in urls" :key="index" class="queue-url-row">
          <label :for="`queue-url-${index}`" class="queue-number">{{index+1}}<span class="sr-only">号视频链接</span></label>
          <input :id="`queue-url-${index}`" v-model="urls[index]" type="url" placeholder="https://…" :disabled="submitting" autocomplete="off" @input="editDraft" />
          <button type="button" class="icon-button" :disabled="urls.length===1||submitting" :aria-label="`移除第 ${index+1} 行`" @click="removeRow(index)"><Icon name="x" /></button>
        </div>
        <button type="button" class="text-button queue-add" :disabled="urls.length>=10||submitting" @click="addRow"><Icon name="plus" />添加一行</button>
        <div class="queue-submit-row"><label for="queue-format">整批下载格式<select id="queue-format" v-model="format" :disabled="submitting" @change="editDraft"><option value="bestvideo+bestaudio/best">最佳画质（合并音视频）</option><option value="best">最佳单文件视频</option><option value="bestaudio/best">最佳音频</option></select></label><button class="button primary" :disabled="submitting||accepted"><span v-if="submitting" class="spinner"></span><Icon v-else name="download" />{{submitting?'正在提交…':accepted?'此批次已入队':'加入下载队列'}}</button></div>
        <p v-if="submitError" class="inline-error queue-message" role="alert">{{submitError}}</p><p v-if="notice" class="queue-notice queue-message" role="status">{{notice}}</p>
      </form>
      <div class="queue-section-heading queue-list-heading"><div><h2>我的任务</h2><p class="muted tiny">{{activeCount?`${activeCount} 项正在处理，实时更新；断线时每 3 秒查询状态`:'文件会逐个领取，不打包 ZIP'}}</p></div><button class="button outline" :disabled="loading" @click="load()"><Icon name="rotate-cw" />{{loading?'正在刷新…':'刷新列表'}}</button></div>
      <p class="queue-help muted">“服务端已准备好”表示可以领取文件。交给浏览器后，请在浏览器下载列表中确认是否已保存到本机。</p>
      <div v-if="listError" class="inline-error queue-message" role="alert">{{listError}} <button class="text-button" :disabled="loading" @click="load()">重试加载</button></div>
      <div v-if="loading&&!items.length" class="empty-state" role="status"><span class="spinner"></span><p>正在读取任务…</p></div>
      <div v-else-if="loaded&&!items.length&&!listError" class="empty-state"><Icon name="download" /><h2>还没有后台任务</h2><p>在上方添加链接，就可以开始第一批下载。</p></div>
      <ul v-if="items.length" class="queue-list" :aria-busy="loading">
        <li v-for="task in items" :key="task.id" class="queue-task">
          <div class="queue-task-meta"><span class="tag">{{kinds[task.kind]||task.kind}}</span><span class="queue-state" :class="`state-${task.state}`">{{statuses[task.state]||task.state}}</span><time class="muted tiny" :datetime="task.created_at">{{date(task.created_at)}}</time></div>
          <h3 class="queue-task-title">{{task.url||task.id}}</h3>
          <p class="queue-task-stage">{{task.kind==='download'&&task.state==='succeeded'?(task.file_available&&!expiry(task)?'服务端已准备好，等待领取':'文件已过期或不可领取，请重新添加链接下载'):stages[task.stage]||task.stage}}</p>
          <div v-if="activeStates.has(task.state)&&task.stage==='downloading'&&task.progress?.downloaded_bytes!=null" class="queue-progress">
            <p class="muted tiny">当前轨道<span v-if="task.progress.track_id"> {{task.progress.track_id}}</span> · 已下载 {{bytes(task.progress.downloaded_bytes)}}<template v-if="task.progress.total_bytes>0"> / {{task.progress.total_is_estimate?'预计 ':''}}{{bytes(task.progress.total_bytes)}}</template><span v-if="task.progress.speed_bytes_per_second!=null"> · {{bytes(task.progress.speed_bytes_per_second)}}/s</span></p>
            <template v-if="trackPercent(task)!==null"><progress :value="trackPercent(task)" max="100" :aria-label="`当前轨道${task.progress.total_is_estimate?'估计':''}下载进度`"></progress><span class="tiny">{{task.progress.total_is_estimate?'估计 ':''}}{{trackPercent(task).toFixed(1)}}%（当前轨道）</span></template>
          </div>
          <p v-if="activeStates.has(task.state)&&!isTaskConnected(task.id)" class="muted tiny">实时连接恢复中，暂时每 3 秒查询状态。</p>
          <p v-if="task.error" class="inline-error queue-message">{{task.error.message}} <span class="tiny">（{{task.error.code}}）</span></p>
          <p v-if="task.file_available&&task.file_expires_at" class="muted tiny">文件保留至 {{date(task.file_expires_at)}}，请及时领取。</p>
          <div class="queue-task-actions">
            <button v-if="activeStates.has(task.state)" class="button outline" :disabled="!!busy[task.id]||task.state==='cancelling'" @click="act(task,'cancel')">{{busy[task.id]==='cancel'||task.state==='cancelling'?'正在取消…':'取消任务'}}</button>
            <button v-if="task.kind==='download'&&retryStates.has(task.state)" class="button outline" :disabled="!!busy[task.id]" @click="act(task,'retry')"><Icon name="rotate-cw" />{{busy[task.id]==='retry'?'正在重试…':'重新尝试'}}</button>
            <template v-if="task.kind!=='download'&&retryStates.has(task.state)"><a v-if="task.url" class="button outline" :href="'/workbench?url='+encodeURIComponent(task.url)">返回视频字幕操作<Icon name="arrow-right" /></a><button v-else class="button outline" @click="emit('close')">返回视频入口</button></template>
            <button v-if="task.kind==='download'&&task.state==='succeeded'&&task.file_available&&!expiry(task)" class="button primary" :disabled="!!receiving" @click="receive(task)"><span v-if="receiving===task.id" class="spinner"></span><Icon v-else name="download" />{{receiving===task.id?'正在领取…':handedOff[task.id]?'再次领取文件':'领取文件'}}</button>
            <button v-if="task.kind!=='download'&&task.state==='succeeded'&&task.result_id" class="button primary" @click="emit('open-result',{kind:task.kind,id:task.id})">打开字幕结果<Icon name="arrow-right" /></button>
          </div>
          <p v-if="task.kind!=='download'&&retryStates.has(task.state)" class="muted queue-message">{{task.kind==='translation'?'请回到原视频的字幕区域，重新选择字幕来源和目标语言后发起翻译。':'请回到原视频的字幕区域，重新发起转写。'}}{{task.url?'':'请先在视频入口粘贴原视频链接。'}}</p>
          <p v-if="handedOff[task.id]" class="queue-notice queue-message" role="status">文件已交给浏览器处理，请查看浏览器下载列表确认保存结果。</p>
          <p v-if="actionErrors[task.id]" class="inline-error queue-message" role="alert">{{actionErrors[task.id]}}</p>
        </li>
      </ul>
      <div v-if="cursor&&items.length" class="queue-more"><button class="button outline" :disabled="loading" @click="load(true)">{{loading?'正在加载…':'加载更多（每页 20 条）'}}</button></div>
    </template>
  </section>
</template>

<style scoped>
.queue-progress{margin-top:10px}.queue-progress progress{display:block;width:100%;height:8px;margin:8px 0;accent-color:var(--primary)}
.queue-card{padding:32px;min-width:0}.queue-heading,.queue-section-heading{display:flex;justify-content:space-between;align-items:center;gap:20px}.queue-heading{margin-bottom:28px}.queue-heading h1{font-size:30px;margin-top:6px}.queue-section-heading h2{font-size:22px}.queue-form{padding:24px;background:var(--surface);border:1px solid var(--line);border-radius:16px}.queue-help{font-size:15px;margin:12px 0 20px}.queue-url-row{display:flex;gap:10px;align-items:center;margin-top:10px}.queue-number{width:22px;flex-shrink:0;text-align:center;font-size:15px;color:var(--muted)}.queue-url-row input{min-width:0;flex:1;width:100%;min-height:48px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:white;font-size:16px}.queue-url-row .icon-button{min-width:44px;min-height:44px;flex-shrink:0}.queue-add{margin:16px 0}.queue-submit-row{display:flex;justify-content:space-between;align-items:flex-end;gap:20px}.queue-submit-row label{display:grid;gap:8px;min-width:0;font-size:15px;font-weight:600}.queue-submit-row select{font-size:16px;min-height:48px;padding:10px 36px 10px 12px;max-width:100%;border:1px solid var(--line);border-radius:10px;background:white;font-weight:400}.queue-submit-row>.button{min-height:48px;flex-shrink:0}.queue-message{font-size:15px;margin-top:12px;overflow-wrap:anywhere}.queue-notice{color:var(--primary-dark)}.queue-list-heading{margin-top:32px}.queue-list{list-style:none;margin:0;padding:0}.queue-task{padding:24px 0;border-top:1px solid var(--line)}.queue-task-meta{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.queue-task-meta time{margin-left:auto}.queue-state{font-size:14px;font-weight:600}.state-running,.state-queued,.state-cancelling{color:var(--primary-dark)}.state-failed,.state-interrupted{color:var(--error)}.queue-task-title{font-size:18px;font-weight:600;overflow-wrap:anywhere;margin-top:14px;line-height:1.6}.queue-task-stage{font-size:16px;margin-top:6px}.queue-task-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}.queue-more{text-align:center;margin-top:20px}.empty-state>.button{margin-top:20px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:640px){.queue-card{padding:22px 20px}.queue-heading{align-items:flex-start;gap:8px}.queue-heading h1{font-size:26px}.queue-heading>.button{padding-inline:10px;flex-shrink:0}.queue-form{padding:16px}.queue-section-heading{gap:12px}.queue-section-heading h2{font-size:20px}.queue-url-row{gap:6px}.queue-number{width:16px}.queue-url-row input{padding-inline:10px}.queue-submit-row{align-items:stretch;flex-direction:column;gap:16px}.queue-submit-row select{width:100%}.queue-submit-row>.button{justify-content:center}.queue-task-meta time{margin-left:0;width:100%}.queue-list-heading>.button{padding-inline:12px}.queue-task-title{font-size:16px}.queue-task-actions .button{min-height:44px}.queue-help{font-size:15px}}
</style>
