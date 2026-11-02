<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import AppHeader from './components/AppHeader.vue'
import AppFooter from './components/AppFooter.vue'
import HeroSection from './components/HeroSection.vue'
import HomeContent from './components/HomeContent.vue'
import {updatePageMetadata} from './pageMetadata.js'
import VideoResult from './components/VideoResult.vue'
import VideoSummary from './components/VideoSummary.vue'
import AuthModal from './components/AuthModal.vue'
import HistoryPanel from './components/HistoryPanel.vue'
import BatchQueue from './components/BatchQueue.vue'
import BillingPanel from './components/BillingPanel.vue'
import {createBatch,fetchTaskResult} from './api/tasks.js'
import {fetchHistoryDetail,createDownloadHistory,recordDownloadEvent} from './api/history.js'
import {useAuth} from './composables/useAuth.js'
import { parseVideo, downloadVideo } from './api/video.js'

const workbench=ref(location.pathname.startsWith('/workbench'))
const currentUrl=ref(new URLSearchParams(location.search).get('url') || '')
const video=ref(null), loading=ref(false), error=ref('')
const downloading=ref(false), downloadStatus=ref(''), downloadError=ref(''), downloadWarning=ref('')
const queueing=ref(false)
let backgroundRequest=null
const hero=ref(null), notice=ref(''), noticeDialog=ref(null)
const auth=useAuth(),authVisible=ref(false),loggingOut=ref(false)
const historyVisible=ref(location.pathname.startsWith('/history')),historyKind=ref(new URLSearchParams(location.search).get('kind')==='download'?'download':'summary')
const queueVisible=ref(location.pathname.startsWith('/queue'))
const billingVisible=ref(location.pathname.startsWith('/billing')),billingOrderId=ref(new URLSearchParams(location.search).get('order_id')||'')
const queueTaskView=ref(null),queueTaskError=ref(''),queueTaskLoading=ref(false)
const historyDetail=ref(null),historyLoading=ref(false),historyError=ref(''),historyDownloadNotice=ref('')
const workbenchRestoreOnly=ref(false),workbenchInitialResult=ref(null),workbenchLanguage=ref('zh')
let historyController
function openHistory(navigate=true){
  cancel();billingVisible.value=false;queueVisible.value=false;historyVisible.value=true;workbench.value=false;historyDetail.value=null;historyError.value='';historyLoading.value=false
  if(navigate)history.pushState({},'','/history?kind='+historyKind.value)
  updatePageMetadata();window.scrollTo(0,0)
}
function openQueue(navigate=true){
  cancel();billingVisible.value=false;queueVisible.value=true;historyVisible.value=false;historyDetail.value=null;workbench.value=false;queueTaskView.value=null;queueTaskError.value=''
  if(navigate)history.pushState({},'','/queue')
  updatePageMetadata();window.scrollTo(0,0)
}
function openBilling(navigate=true){
  cancel();billingVisible.value=true;queueVisible.value=false;historyVisible.value=false;workbench.value=false;queueTaskView.value=null
  if(navigate){billingOrderId.value='';history.pushState({},'','/billing')}
  else billingOrderId.value=new URLSearchParams(location.search).get('order_id')||''
  updatePageMetadata();window.scrollTo(0,0)
}
async function openTaskResult({id}){
  historyController?.abort();const controller=new AbortController();historyController=controller
  const version=auth.sessionVersion.value;queueTaskView.value=null;queueTaskError.value='';queueTaskLoading.value=true
  try{
    const data=await fetchTaskResult(id,controller.signal)
    if(!controller.signal.aborted&&version===auth.sessionVersion.value){
      queueTaskView.value={id,url:data.url,title:data.source?.video_title||'已保存字幕',language:data.source?.language||'zh',
        summary:data.source?.summary_result,transcription:data.kind==='transcription'?data.result:data.source?.transcription_result,
        translation:data.kind==='translation'?data.result:null}
    }
  }catch(cause){if(!controller.signal.aborted&&version===auth.sessionVersion.value)queueTaskError.value=cause.message}
  finally{if(historyController===controller)queueTaskLoading.value=false}
}
function selectHistoryKind(kind){
  historyKind.value=kind;historyController?.abort();historyDetail.value=null;historyError.value='';historyLoading.value=false
  history.replaceState({},'','/history?kind='+kind)
}
function historyDeleted(kind,id){if(historyDetail.value?.kind===kind&&historyDetail.value.id===id)openHistory()}
function historyResultSaved(result){if(historyDetail.value?.kind==='summary')historyDetail.value={...historyDetail.value,result:{...historyDetail.value.result,...result}}}
async function openHistoryItem(kind,id,navigate=true){
  historyController?.abort();const controller=new AbortController();historyController=controller
  const version=auth.sessionVersion.value
  billingVisible.value=false;queueVisible.value=false;historyVisible.value=true;workbench.value=false;historyKind.value=kind;historyDetail.value=null;historyLoading.value=true;historyError.value=''
  if(navigate)history.pushState({},'',`/history/${kind}/${encodeURIComponent(id)}`)
  updatePageMetadata()
  try {const detail=await fetchHistoryDetail(kind,id,controller.signal);if(!controller.signal.aborted&&version===auth.sessionVersion.value)historyDetail.value=detail}
  catch(cause){if(!controller.signal.aborted&&version===auth.sessionVersion.value)historyError.value=cause.message}
  finally{if(historyController===controller)historyLoading.value=false}
}
watch(()=>auth.sessionVersion.value,()=>{historyController?.abort();historyDetail.value=null;workbenchInitialResult.value=null;queueTaskView.value=null;queueTaskError.value='';queueTaskLoading.value=false;historyLoading.value=false;historyError.value='';historyDownloadNotice.value=''}, {flush:'sync'})
watch([()=>auth.authReady.value,()=>auth.user.value],()=>{
  const match=location.pathname.match(/^\/history\/(summary|download)\/([^/]+)$/)
  if(historyVisible.value&&auth.authReady.value&&auth.user.value&&match)openHistoryItem(match[1],match[2],false)
})
async function logout(){
  loggingOut.value=true
  try {await auth.logout()}catch(cause){showNotice(cause.message)}
  finally {loggingOut.value=false}
}
let parseController, downloadController
let quotaTimer
function scheduleQuotaRefresh(){
  clearTimeout(quotaTimer)
  if(!auth.user.value||!auth.quota.value)return
  const delay=Date.parse(auth.quota.value.reset_at)-Date.parse(auth.quota.value.server_time)
  quotaTimer=setTimeout(()=>auth.refreshQuota(),Math.max(1000,delay+250))
}
watch(()=>auth.quota.value,scheduleQuotaRefresh)
function focus(){
  if(!auth.authReady.value)return
  if(auth.user.value)auth.refreshQuota()
  else if(auth.authError.value)auth.restoreSession()
}

async function showNotice(message='视频解析和单条下载无需登录；登录后可以查看历史与后台任务。') {
  notice.value=message
  await nextTick()
  if (!noticeDialog.value.open) noticeDialog.value.showModal()
}
async function backgroundDownload(formatId){
  if(!auth.user.value){authVisible.value=true;return}
  if(queueing.value)return
  const url=currentUrl.value,version=auth.sessionVersion.value
  if(!backgroundRequest||backgroundRequest.url!==url||backgroundRequest.formatId!==formatId||backgroundRequest.version!==version)backgroundRequest={url,formatId,version,key:crypto.randomUUID()}
  queueing.value=true;downloadError.value=''
  try{
    await createBatch({request_key:backgroundRequest.key,items:[{url,format_id:formatId}]})
    if(version===auth.sessionVersion.value&&url===currentUrl.value){backgroundRequest=null;openQueue()}
  }catch(cause){if(version===auth.sessionVersion.value&&url===currentUrl.value)downloadError.value=cause.message}
  finally{queueing.value=false}
}
function cancel() { parseController?.abort(); downloadController?.abort();historyController?.abort() }
function home() {
  cancel();billingVisible.value=false;queueVisible.value=false;queueTaskView.value=null;historyVisible.value=false;historyDetail.value=null; workbench.value=false; video.value=null; loading.value=false
  history.pushState({},'', '/'); updatePageMetadata(); window.scrollTo(0,0)
}
async function start() { if(workbench.value||historyVisible.value||queueVisible.value||billingVisible.value) home(); await nextTick(); hero.value.focus() }
async function example() { if(workbench.value||historyVisible.value||queueVisible.value||billingVisible.value) home(); await nextTick(); hero.value.example('bilibili'); window.scrollTo(0,0) }
async function parse(url, navigate=true,options={}) {
  cancel(); const controller=new AbortController(); parseController=controller
  billingVisible.value=false;queueVisible.value=false;historyVisible.value=false;historyDetail.value=null;workbenchRestoreOnly.value=!!options.restoreOnly;workbenchInitialResult.value=options.result||null;workbenchLanguage.value=options.language||'zh'
  currentUrl.value=url; video.value=null; loading.value=true; error.value=''
  downloading.value=false; downloadStatus.value='';downloadError.value='';downloadWarning.value=''
  if(!workbench.value&&navigate) history.pushState({},'', '/workbench?url='+encodeURIComponent(url))
  else if(navigate) history.replaceState({},'', '/workbench?url='+encodeURIComponent(url))
  workbench.value=true; updatePageMetadata(); window.scrollTo(0,0)
  try {
    const result=await parseVideo(url,controller.signal)
    if(parseController===controller) video.value=result.data
  } catch(cause) {
    if(!controller.signal.aborted&&parseController===controller) error.value=cause.message
  } finally { if(parseController===controller) loading.value=false }
}
async function download(formatId) {
  if(downloading.value) return
  const controller=new AbortController(); downloadController=controller
  downloading.value=true;downloadError.value='';downloadWarning.value=''
  historyDownloadNotice.value=''
  const version=auth.sessionVersion.value
  const recordPromise=auth.user.value?createDownloadHistory({url:currentUrl.value,video_title:(video.value.title||'').slice(0,300),platform:(video.value.platform||'').slice(0,100),format_id:formatId},controller.signal).catch(cause=>{
    if(!controller.signal.aborted&&downloadController===controller&&version===auth.sessionVersion.value)historyDownloadNotice.value='本次未能保存下载历史：'+cause.message
    return null
  }):Promise.resolve(null)
  function recordEvent(event){
    recordPromise.then(record=>{if(record&&version===auth.sessionVersion.value)return recordDownloadEvent(record.id,event)}).catch(cause=>{
      if(downloadController===controller&&version===auth.sessionVersion.value)historyDownloadNotice.value='视频下载不受影响，历史状态暂未更新：'+cause.message
    })
  }
  try {
    if(controller.signal.aborted){recordEvent('cancelled');return}
    const result=await downloadVideo(currentUrl.value,formatId,text=>{if(downloadController===controller)downloadStatus.value=text},controller.signal)
    recordEvent('browser_handoff')
    if(downloadController===controller){downloadStatus.value='文件已交给浏览器保存，请查看浏览器下载列表。';downloadWarning.value=result.warning}
  } catch(cause) {
    recordEvent(controller.signal.aborted?'cancelled':'failed')
    if(!controller.signal.aborted&&downloadController===controller){downloadStatus.value='';downloadError.value=cause.message}
  } finally { if(downloadController===controller) downloading.value=false }
}
function popstate() {
  const url=new URLSearchParams(location.search).get('url') || ''
  if(location.pathname.startsWith('/billing'))openBilling(false)
  else if(location.pathname.startsWith('/queue'))openQueue(false)
  else if(location.pathname.startsWith('/history')){openHistory(false);const match=location.pathname.match(/^\/history\/(summary|download)\/([^/]+)$/);if(match&&auth.user.value)openHistoryItem(match[1],match[2],false)}
  else if(location.pathname.startsWith('/workbench')&&url) parse(url,false)
  else {cancel();billingVisible.value=false;queueVisible.value=false;historyVisible.value=false;historyDetail.value=null;workbench.value=false;video.value=null;loading.value=false}
  updatePageMetadata()
}
onMounted(()=>{auth.restoreSession();window.addEventListener('popstate',popstate);window.addEventListener('focus',focus);if(workbench.value&&currentUrl.value)parse(currentUrl.value,false)})
onUnmounted(()=>{cancel();clearTimeout(quotaTimer);window.removeEventListener('popstate',popstate);window.removeEventListener('focus',focus)})
</script>

<template>
  <a class="skip-link" href="#main">跳到主要内容</a>
  <AppHeader :workbench="workbench" :user="auth.user.value" :auth-ready="auth.authReady.value" :auth-error="auth.authError.value" :logging-out="loggingOut" @billing="openBilling" @queue="openQueue" @history="openHistory" @login="authVisible=true" @logout="logout" @retry="auth.restoreSession" @home="home" @start="start" @notice="showNotice" />
  <main v-if="billingVisible" id="main" class="workspace-main"><div class="container"><BillingPanel :key="billingOrderId" :order-id="billingOrderId" @close="home" @login="authVisible=true" @entitlement-updated="auth.restoreSession" /></div></main>
  <main v-else-if="queueVisible" id="main" class="workspace-main"><div class="container"><BatchQueue @open-result="openTaskResult" @close="home" @login="authVisible=true" /><p v-if="queueTaskLoading" role="status">正在读取保存字幕…</p><p v-if="queueTaskError" role="alert">{{queueTaskError}}</p><div v-if="queueTaskView&&auth.user.value" class="history-detail"><VideoSummary :key="queueTaskView.id+':'+auth.sessionVersion.value" :video-url="queueTaskView.url" :video-title="queueTaskView.title" :language="queueTaskView.language" :initial-result="queueTaskView.summary" :initial-transcription="queueTaskView.transcription" :initial-translation="queueTaskView.translation" restore-only @login="authVisible=true" @notice="openBilling" /></div></div></main>
  <main v-else-if="historyVisible" id="main" class="workspace-main"><div class="container">
    <HistoryPanel :selected-kind="historyKind" @update:selected-kind="selectHistoryKind" @open="openHistoryItem" @deleted="historyDeleted" @close="home" @login="authVisible=true" />
    <p v-if="historyLoading" class="loading-caption" role="status">正在读取保存内容…</p>
    <div v-else-if="historyError" class="card empty-state" role="alert"><p>{{historyError}}</p><button class="button outline" @click="popstate">重试读取</button></div>
    <section v-else-if="historyDetail&&auth.user.value" class="history-detail">
      <div class="card history-detail-heading"><div><p class="kicker">SAVED FOR YOU</p><h2>{{historyDetail.video_title}}</h2><p class="history-source">{{historyDetail.url}}</p><p>此处读取已保存内容，不重新生成，也不使用总结额度。</p></div><button class="button outline" @click="parse(historyDetail.url,true,{restoreOnly:true,result:historyDetail.result,language:historyDetail.language})">重新解析下载信息</button></div>
      <VideoSummary v-if="historyDetail.kind==='summary'" :key="historyDetail.id+':'+auth.sessionVersion.value" :video-url="historyDetail.url" :video-title="historyDetail.video_title" :platform="historyDetail.platform" :language="historyDetail.language" :initial-result="historyDetail.result" restore-only @saved="historyResultSaved" @login="authVisible=true" @notice="openBilling" />
    </section>
  </div></main>
  <main v-else-if="!workbench" id="main"><HomeContent ref="hero" @parse="parse" @notice="showNotice" @example="example" @start="start" @upgrade="openBilling" /></main>
  <main v-else id="main" class="workspace-main"><div class="container">
    <div class="workspace-topline"><a href="/" class="back-link" @click.prevent="home"><Icon name="arrow-left" />返回首页</a><div class="prototype-label"><span class="status-dot"></span>从保存，到理解</div></div>
    <HeroSection ref="hero" compact :initial-url="currentUrl" :loading="loading" @parse="parse" @notice="showNotice" />
    <div class="workspace-heading"><div><p class="kicker">YOUR VIDEO, REIMAGINED</p><h1>把好内容，变成你的知识。</h1></div></div>
    <div v-if="loading" aria-live="polite"><div class="loading-caption"><span class="spinner"></span>正在识别平台并获取视频信息…</div><div class="parse-loading"><div v-for="i in 2" :key="i" class="card skeleton-card"><div class="skeleton" :class="i===1?'cover-skeleton':'short'"></div><div class="skeleton medium"></div><div class="skeleton short"></div><div class="skeleton"></div></div></div></div>
    <div v-else-if="error" class="card empty-state" role="alert"><span class="empty-illustration"><Icon name="link" /></span><h3>这段视频，暂时没找到。</h3><p>{{error}}</p><button class="button primary" @click="parse(currentUrl)"><Icon name="rotate-cw" />重新解析</button><button class="text-button" @click="example">换一个视频</button></div>
    <p v-if="historyDownloadNotice" role="status">{{historyDownloadNotice}}</p>
    <div v-if="!loading&&!error&&video" class="workspace-grid"><VideoResult :key="currentUrl" :video="video" :downloading="downloading" :queueing="queueing" :status="downloadStatus" :error="downloadError" :warning="downloadWarning" @download="download" @background-download="backgroundDownload" /><VideoSummary :key="currentUrl+':'+auth.sessionVersion.value" :video-url="currentUrl" :video-title="video.title" :platform="video.platform" :language="workbenchLanguage" :initial-result="workbenchInitialResult" :restore-only="workbenchRestoreOnly" @login="authVisible=true" @notice="openBilling" /></div>
    <div v-else-if="!loading&&!error&&!video" class="card empty-state"><h3>从一个视频链接开始。</h3><p>在上方粘贴视频链接，查看视频信息并选择格式下载。</p></div>
  </div></main>
  <AppFooter />
  <AuthModal :visible="authVisible" @close="authVisible=false" />
  <dialog ref="noticeDialog" aria-labelledby="notice-title"><button class="icon-button dialog-close" aria-label="关闭提示" @click="noticeDialog.close()"><Icon name="x" /></button><div class="dialog-inner"><p class="dialog-eyebrow">MORE ROOM TO EXPLORE</p><h2 id="notice-title">留一点期待。</h2><p class="dialog-description notice-description">{{notice}}</p><button class="button primary full" style="margin-top:24px" @click="noticeDialog.close()">继续探索<Icon name="arrow-right" /></button></div></dialog>
</template>
