<script setup>
import { computed, ref, nextTick, onMounted, onUnmounted, defineAsyncComponent, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { summarizeVideo, chatWithVideo, fetchSummaryResult } from '../api/summarize.js'
import { saveFile, safeFilename, subtitleFile } from '../downloads.js'
import {useAuth,updateQuota} from '../composables/useAuth.js'
import {translateSubtitles,fetchTranslation,createTranscription,fetchTranscription} from '../api/subtitles.js'
import {fetchTask,fetchTaskResult,cancelTask} from '../api/tasks.js'
import {useTaskProgress} from '../composables/useTaskProgress.js'

const MindmapView=defineAsyncComponent(()=>import('./MindmapView.vue'))
const props=defineProps({videoUrl:{type:String,required:true},videoTitle:String,platform:String,language:{type:String,default:'zh'},initialResult:Object,initialTranscription:Object,initialTranslation:Object,restoreOnly:Boolean})
const emit=defineEmits(['notice','login','saved','transcription-saved','translation-saved'])
const {user,quota,authReady,authError,sessionVersion,restoreSession,refreshQuota}=useAuth()
const initialSessionVersion=sessionVersion.value
const tabs=[['summary','file-text','总结摘要'],['subtitle','captions','字幕文本'],['mindmap','git-branch','思维导图'],['qa','message-circle','AI 问答']]
const active=ref(tabs.some(tab=>tab[0]===location.hash.slice(1))?location.hash.slice(1):'summary')
const tabButtons=ref([]),loading=ref(false),status=ref('等待开始'),error=ref('')
const subtitle=ref(null),summary=ref(''),mindmap=ref(''),expanded=ref(false),subtitleFormat=ref('srt')
const messages=ref([]),question=ref(''),chatBusy=ref(false),chatList=ref(null)
const notification=ref('')
const restoreFailed=ref(false)
let summaryController,chatController,run=0,toastTimer,attempted=false,lastSuccessful=null
const summaryResultId=ref(null),transcription=ref(null),translation=ref(null),translationSource=ref('')
const sourceKind=ref('summary'),targetLanguage=ref('en'),showTranslation=ref(false)
const extensionError=ref(''),translationBusy=ref(false),transcriptionBusy=ref(false),translationStatus=ref(''),transcriptionStatus=ref('')
const translationTask=ref(null),transcriptionTask=ref(null)
const {connectTaskProgress,closeTaskProgress,isTaskConnected}=useTaskProgress()
let translationController,transcriptionController,translationReadController,extensionReadController,translationRead=0
const taskWatches=new Map()
const sourceSubtitle=computed(()=>sourceKind.value==='transcription'?transcription.value?.subtitle:subtitle.value)
const sourceId=computed(()=>sourceKind.value==='transcription'?transcription.value?.id:summaryResultId.value)
const matchingTranslation=computed(()=>translation.value&&translation.value.source_kind===sourceKind.value&&translation.value.source_id===sourceId.value&&translation.value.target_language===targetLanguage.value&&translationSource.value===JSON.stringify(sourceSubtitle.value)?translation.value:null)
const displayedSubtitle=computed(()=>showTranslation.value&&matchingTranslation.value?matchingTranslation.value.subtitle:sourceSubtitle.value)
const displayedSegments=computed(()=>displayedSubtitle.value?.segments||[])
const visibleSubtitleSegments=computed(()=>expanded.value?displayedSegments.value:displayedSegments.value.slice(0,6))
const subtitleSourceLabel=computed(()=>{
  if(showTranslation.value&&matchingTranslation.value)return '翻译字幕'
  if(sourceSubtitle.value?.source==='transcription'||sourceKind.value==='transcription')return '转写字幕'
  return sourceSubtitle.value?.subtitle_type==='manual'?'平台人工字幕':'平台自动字幕'
})
const taskStageLabels={queued:'等待处理',running:'正在处理',extracting_audio:'正在提取音频',downloading:'正在下载音频',transcribing:'正在转写',translating:'正在逐段翻译',cancelling:'正在取消',succeeded:'已完成',failed:'处理失败',cancelled:'已取消',interrupted:'任务已中断'}
function stopTaskWatch(id){const entry=taskWatches.get(id);if(!entry)return;taskWatches.delete(id);clearTimeout(entry.timer);closeTaskProgress(id)}
function stopExtensions(){
  translationController?.abort();transcriptionController?.abort();translationReadController?.abort();extensionReadController?.abort();translationRead++
  for(const id of taskWatches.keys())stopTaskWatch(id)
  translationBusy.value=false;transcriptionBusy.value=false;translationTask.value=null;transcriptionTask.value=null
}
function acceptExtensionResult(kind,result){
  if(kind==='translation'){translation.value=result;translationSource.value=JSON.stringify(sourceSubtitle.value);showTranslation.value=true;emit('translation-saved',result)}
  else {transcription.value=result;sourceKind.value='transcription';showTranslation.value=false;emit('transcription-saved',result)}
}
function extensionFailure(cause,version){extensionError.value=cause.message;if(cause.quota)updateQuota(cause.quota,version)}
function monitorExtensionTask(task,kind,current,version,controller){
  const busy=kind==='translation'?translationBusy:transcriptionBusy,taskRef=kind==='translation'?translationTask:transcriptionTask,stage=kind==='translation'?translationStatus:transcriptionStatus
  const entry={timer:null,version:-1,finishing:false};taskWatches.set(task.id,entry);taskRef.value=task.id
  const valid=()=>taskWatches.get(task.id)===entry&&isCurrent(current,version)&&!controller.signal.aborted
  async function accept(snapshot){
    if(!valid()||entry.finishing||snapshot.version<entry.version)return
    entry.version=snapshot.version;stage.value=taskStageLabels[snapshot.stage]||taskStageLabels[snapshot.state]||'处理中'
    if(!['succeeded','failed','cancelled','interrupted'].includes(snapshot.state))return
    entry.finishing=true
    try {
      if(snapshot.state==='succeeded'){
        const data=await fetchTaskResult(task.id,controller.signal)
        if(!valid())return
        acceptExtensionResult(kind,data.result)
      } else if(snapshot.state!=='cancelled')extensionError.value=snapshot.error_message||'任务未完成，原文和上次成功结果已保留；本次不计成功额度。'
    } catch(cause){if(valid())extensionFailure(cause,version)}
    finally {if(valid()){busy.value=false;taskRef.value=null;stopTaskWatch(task.id);refreshQuota()}}
  }
  async function poll(){
    if(!valid())return
    try {if(!isTaskConnected(task.id))await accept(await fetchTask(task.id,controller.signal))}
    catch(cause){if(valid()){extensionError.value='任务进度暂时无法读取，可稍后从任务队列恢复。'+cause.message;if([401,403,404].includes(cause.status)){busy.value=false;taskRef.value=null;stopTaskWatch(task.id)}}}
    if(valid())entry.timer=setTimeout(poll,3000)
  }
  connectTaskProgress(task.id,accept);accept(task);entry.timer=setTimeout(poll,3000)
}
async function resolveSourceId(signal,current,version){
  if(sourceId.value)return sourceId.value
  if(sourceKind.value==='transcription')throw new Error('尚无成功保存的转写字幕，请先完成转写。')
  const result=await fetchSummaryResult(props.videoUrl,props.language,signal)
  if(!isCurrent(current,version)||signal.aborted)return null
  if(!result?.result_id)throw new Error('没有找到已保存的字幕来源，未发起翻译。请先成功生成并保存总结。')
  summaryResultId.value=result.result_id
  if(lastSuccessful)lastSuccessful={...lastSuccessful,result_id:result.result_id}
  return result.result_id
}
async function restoreTranslation(){
  translationReadController?.abort()
  if(translationBusy.value||!authReady.value||!user.value||!sourceId.value||!sourceSubtitle.value?.has_subtitle)return
  const controller=new AbortController(),read=++translationRead,current=run,version=sessionVersion.value;translationReadController=controller
  const kind=sourceKind.value,id=sourceId.value,target=targetLanguage.value
  try {
    const result=await fetchTranslation(kind,id,target,controller.signal)
    if(isCurrent(current,version)&&read===translationRead&&!controller.signal.aborted){translation.value=result;translationSource.value=JSON.stringify(sourceSubtitle.value);showTranslation.value=!!result}
  }catch(cause){if(isCurrent(current,version)&&read===translationRead&&!controller.signal.aborted)extensionFailure(cause,version)}
}
async function startTranslation(force=false){
  if(!user.value){emit('login');return}
  if(translationBusy.value||!sourceSubtitle.value?.has_subtitle)return
  translationReadController?.abort();translationRead++
  const controller=new AbortController(),current=run,version=sessionVersion.value;translationController=controller
  translationBusy.value=true;extensionError.value='';translationStatus.value='正在查询已保存译文'
  const kind=sourceKind.value,target=targetLanguage.value
  try {
    const id=await resolveSourceId(controller.signal,current,version)
    if(!isCurrent(current,version)||!id||controller.signal.aborted)return
    if(!force){
      const cached=await fetchTranslation(kind,id,target,controller.signal)
      if(!isCurrent(current,version)||controller.signal.aborted)return
      if(cached){acceptExtensionResult('translation',cached);translationStatus.value='已恢复译文（不计次）';return}
    }
    const result=await translateSubtitles({request_key:crypto.randomUUID(),source_kind:kind,source_id:id,target_language:target,force},controller.signal)
    if(!isCurrent(current,version)||controller.signal.aborted)return
    if(result.cached){acceptExtensionResult('translation',result.result);translationStatus.value='已恢复译文（不计次）'}
    else monitorExtensionTask(result.task,'translation',current,version,controller)
    refreshQuota()
  }catch(cause){if(isCurrent(current,version)&&!controller.signal.aborted)extensionFailure(cause,version)}
  finally {if(isCurrent(current,version)&&!translationTask.value)translationBusy.value=false}
}
async function restoreTranscription(){
  extensionReadController?.abort()
  if(!authReady.value||!user.value)return
  const controller=new AbortController(),current=run,version=sessionVersion.value;extensionReadController=controller
  try {
    const result=await fetchTranscription(props.videoUrl,controller.signal)
    if(isCurrent(current,version)&&!controller.signal.aborted&&result){transcription.value=result;if(!subtitle.value?.has_subtitle)sourceKind.value='transcription'}
  }catch(cause){if(isCurrent(current,version)&&!controller.signal.aborted)extensionFailure(cause,version)}
}
async function startTranscription(force=false){
  if(!user.value){emit('login');return}
  if(transcriptionBusy.value)return
  const controller=new AbortController(),current=run,version=sessionVersion.value;transcriptionController=controller
  transcriptionBusy.value=true;extensionError.value='';transcriptionStatus.value='正在提交转写'
  try {
    const result=await createTranscription({request_key:crypto.randomUUID(),url:props.videoUrl,language:'auto',force},controller.signal)
    if(!isCurrent(current,version)||controller.signal.aborted)return
    if(result.cached){acceptExtensionResult('transcription',result.result);transcriptionStatus.value='已恢复转写（不扣时长）'}
    else monitorExtensionTask(result.task,'transcription',current,version,controller)
    refreshQuota()
  }catch(cause){if(isCurrent(current,version)&&!controller.signal.aborted)extensionFailure(cause,version)}
  finally {if(isCurrent(current,version)&&!transcriptionTask.value)transcriptionBusy.value=false}
}
async function cancelExtension(kind){
  const id=(kind==='translation'?translationTask:transcriptionTask).value
  if(!id)return
  const current=run,version=sessionVersion.value,controller=kind==='translation'?translationController:transcriptionController
  try {await cancelTask(id,controller.signal);if(isCurrent(current,version))toast('已请求取消，任务停止后释放未成功额度。')}
  catch(cause){if(isCurrent(current,version)&&!controller.signal.aborted)extensionFailure(cause,version)}
}
const needsLogin=computed(()=>!authReady.value||!user.value)
const resetLabel=computed(()=>quota.value?new Date(quota.value.reset_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}):'北京时间次日零点')
function isCurrent(current,version){return current===run&&version===sessionVersion.value}
function quotaMessage(kind){
  const item=quota.value?.[kind]
  if(!item)return '额度暂不可用，请刷新额度后重试。'
  return item.used>=item.limit?`今日${kind==='summary'?'总结':'问答'}次数已用完，将于北京时间 ${resetLabel.value} 恢复。`:'可用名额正在其他请求中处理，请等待后刷新额度。'
}
function streamFailure(raw){const result=JSON.parse(raw);return result.message+(['NO_SUBTITLE','AI_FAILED'].includes(result.code)?' 本次未完整生成，不计成功次数。':'')}
function showSavedResult(result){subtitle.value=result.subtitle;summary.value=result.summary;mindmap.value=result.mindmap;summaryResultId.value=result.result_id||null;sourceKind.value='summary';showTranslation.value=false}
function keepPreviousResult(){if(lastSuccessful){showSavedResult(lastSuccessful);status.value='已保留上次成功结果'}}
async function restoreSummary(){
  if(!authReady.value||!user.value||loading.value)return
  if(props.restoreOnly){
    attempted=true
    if(initialSessionVersion===sessionVersion.value&&props.initialResult){lastSuccessful=props.initialResult;showSavedResult(lastSuccessful);status.value='已恢复保存结果'}
    else if(initialSessionVersion===sessionVersion.value&&(props.initialTranscription||props.initialTranslation)){status.value='已恢复字幕结果';active.value='subtitle'}
    else {status.value='尚无保存结果';error.value='此历史记录没有保存的总结。需要生成时，请主动点击“重新总结”，完整成功才计次。'}
    restoreTranscription()
    return
  }
  attempted=true;restoreFailed.value=false;error.value='';loading.value=true;status.value='正在恢复保存结果'
  const current=++run,version=sessionVersion.value,controller=new AbortController();summaryController=controller
  try {
    const result=await fetchSummaryResult(props.videoUrl,props.language,controller.signal)
    if(!isCurrent(current,version))return
    if(result){lastSuccessful=result;showSavedResult(result);status.value='已恢复保存结果'}
    else {loading.value=false;await startSummarize()}
  } catch(cause){
    if(isCurrent(current,version)&&!controller.signal.aborted){restoreFailed.value=true;error.value='恢复已有结果失败，请重试恢复；不会自动重新生成或扣次。'+cause.message;status.value='等待重试恢复'}
  } finally {if(isCurrent(current,version)){loading.value=false;restoreTranscription()}}
}
const hasSubtitles=computed(()=>!!subtitle.value?.has_subtitle)
const noSubtitles=computed(()=>subtitle.value?.has_subtitle===false)
const suggestions=['这个视频的核心观点是什么？','帮我梳理一下视频的内容大纲。','作为新手，我应该从哪一步开始？']
const sections=computed(()=>{
  const parts=summary.value.split(/^##\s+/m)
  if(parts.length<2||parts[0].trim())return []
  return parts.slice(1).map(part=>{const end=part.indexOf('\n');return {title:end<0?part:part.slice(0,end),body:end<0?'':part.slice(end+1)}})
})
function renderMarkdown(text) {return DOMPurify.sanitize(marked.parse(text,{gfm:true,breaks:true,async:false}))}
function toast(message) {notification.value=message;clearTimeout(toastTimer);toastTimer=setTimeout(()=>notification.value='',4500)}
function stamp(seconds) {
  const total=Math.floor(seconds),h=Math.floor(total/3600),m=Math.floor(total/60)%60,s=total%60
  return `${h?h+':':''}${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
}
function switchTab(id) {active.value=id;const address=new URL(location.href);address.hash=id;history.replaceState({},'',address)}
function tabKey(event,index) {
  let next
  if(event.key==='ArrowRight')next=(index+1)%tabs.length
  else if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length
  else if(event.key==='Home')next=0
  else if(event.key==='End')next=tabs.length-1
  else return
  event.preventDefault();switchTab(tabs[next][0]);tabButtons.value[next].focus()
}
async function startSummarize(transcriptId=null) {
  if(!authReady.value)return
  if(!user.value){emit('login');return}
  if(loading.value)return
  attempted=true
  restoreFailed.value=false
  if(!quota.value||quota.value.summary.remaining<=0){error.value=quotaMessage('summary');status.value='等待可用额度';return}
  stopExtensions();summaryController?.abort();chatController?.abort()
  const controller=new AbortController(),current=++run,version=sessionVersion.value;summaryController=controller
  loading.value=true;error.value='';summary.value='';mindmap.value='';subtitle.value=null;summaryResultId.value=null
  messages.value=[];chatBusy.value=false;expanded.value=false;status.value='正在提取字幕'
  try {
    await summarizeVideo(props.videoUrl,props.language,{
      start:()=>{if(isCurrent(current,version))refreshQuota()},
      subtitle:data=>{if(!isCurrent(current,version))return;subtitle.value=JSON.parse(data);status.value=hasSubtitles.value?'AI 正在整理':'暂无可用字幕'},
      summary:data=>{if(isCurrent(current,version))summary.value+=JSON.parse(data)},
      mindmap:data=>{if(isCurrent(current,version))mindmap.value=JSON.parse(data).markdown},
      quota:data=>{if(isCurrent(current,version))updateQuota(JSON.parse(data),version)},
      done:()=>{if(isCurrent(current,version)){lastSuccessful={subtitle:subtitle.value,summary:summary.value,mindmap:mindmap.value};status.value='已整理完成';emit('saved',lastSuccessful)}},
      error:data=>{if(isCurrent(current,version)){error.value=streamFailure(data);status.value=noSubtitles.value?'暂无可用字幕':'生成暂时中断';keepPreviousResult()}},
    },controller.signal,{video_title:(props.videoTitle||'').slice(0,300),platform:(props.platform||'').slice(0,100),...(transcriptId?{transcript_id:transcriptId}:{})})
  } catch(cause) {if(isCurrent(current,version)&&!controller.signal.aborted){error.value=cause.message;status.value='生成暂时中断';if(cause.quota)updateQuota(cause.quota,version);keepPreviousResult()}}
  finally {if(isCurrent(current,version)){loading.value=false;refreshQuota();if(!subtitle.value?.has_subtitle){if(transcription.value)sourceKind.value='transcription';else restoreTranscription()}}}
}
async function copySummary() {
  try {await navigator.clipboard.writeText(summary.value);toast('已复制到剪贴板')}
  catch {toast('浏览器不允许复制，请手动选择文字复制。')}
}
function exportSubtitles() {
  const format=subtitleFormat.value
  const label=showTranslation.value&&matchingTranslation.value?`译文-${targetLanguage.value}`:subtitleSourceLabel.value
  saveFile(subtitleFile(displayedSegments.value,format),safeFilename(props.videoTitle)+' - '+label+'.'+format,format==='vtt'?'text/vtt;charset=utf-8':'text/plain;charset=utf-8')
  toast(`已导出 ${format.toUpperCase()} 字幕`)
}
async function scrollChat() {await nextTick();if(chatList.value)chatList.value.scrollTop=chatList.value.scrollHeight}
async function sendQuestion(raw=question.value) {
  const text=raw.trim()
  if(!text||chatBusy.value||!hasSubtitles.value)return
  if(!user.value){emit('login');return}
  if(!quota.value||quota.value.chat.remaining<=0){toast(quotaMessage('chat'));return}
  const current=run,version=sessionVersion.value,controller=new AbortController();chatController=controller
  const history=messages.value.filter(message=>!message.error&&message.content).slice(-20).map(message=>({role:message.role,content:message.content.slice(0,6000)}))
  messages.value.push({role:'user',content:text},{role:'assistant',content:'',loading:true,error:''})
  const answer=messages.value.at(-1)
  question.value='';chatBusy.value=true;scrollChat()
  try {
    await chatWithVideo(props.videoUrl,text,subtitle.value.full_text.slice(0,15000),{
      start:()=>{if(isCurrent(current,version))refreshQuota()},
      answer:data=>{if(isCurrent(current,version)){answer.content+=JSON.parse(data);scrollChat()}},
      quota:data=>{if(isCurrent(current,version))updateQuota(JSON.parse(data),version)},
      done:()=>{if(isCurrent(current,version))answer.loading=false},
      error:data=>{if(isCurrent(current,version))answer.error=streamFailure(data)},
    },controller.signal,history)
  } catch(cause) {if(isCurrent(current,version)&&!controller.signal.aborted){answer.error=cause.message;if(cause.quota)updateQuota(cause.quota,version)}}
  finally {if(isCurrent(current,version)){answer.loading=false;chatBusy.value=false;scrollChat();refreshQuota()}}
}
function retryQuestion(index) {const text=messages.value[index-1].content;messages.value.splice(index-1,2);sendQuestion(text)}
function questionKey(event) {if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();sendQuestion()}}
function hashChanged() {const id=location.hash.slice(1);if(tabs.some(tab=>tab[0]===id))active.value=id}
watch([authReady,user],()=>{if(authReady.value&&user.value&&!attempted)restoreSummary()},{immediate:true})
watch([sourceKind,sourceId,targetLanguage,sourceSubtitle],()=>{showTranslation.value=!!matchingTranslation.value;expanded.value=false;if(!matchingTranslation.value)restoreTranslation()})
watch(()=>props.initialTranscription,result=>{if(result&&initialSessionVersion===sessionVersion.value){transcription.value=result;sourceKind.value='transcription';active.value='subtitle'}},{immediate:true})
watch(()=>props.initialTranslation,result=>{if(result&&initialSessionVersion===sessionVersion.value){translation.value=result;targetLanguage.value=result.target_language;sourceKind.value=result.source_kind;if(result.source_kind==='summary')summaryResultId.value=result.source_id;translationSource.value=JSON.stringify(sourceSubtitle.value);showTranslation.value=true;active.value='subtitle'}},{immediate:true})
watch(sessionVersion,()=>{
  run++;summaryController?.abort();chatController?.abort();stopExtensions();attempted=false;lastSuccessful=null
  subtitle.value=null;summary.value='';mindmap.value='';summaryResultId.value=null;transcription.value=null;translation.value=null;messages.value=[];question.value='';loading.value=false;chatBusy.value=false;error.value='';extensionError.value=''
},{flush:'sync'})
onMounted(()=>{window.addEventListener('hashchange',hashChanged)})
onUnmounted(()=>{run++;stopExtensions();summaryController?.abort();chatController?.abort();clearTimeout(toastTimer);window.removeEventListener('hashchange',hashChanged)})
</script>

<template>
  <section class="card ai-card" aria-label="AI 视频学习助手" :aria-busy="loading">
    <div class="ai-heading"><span class="ai-brand-icon"><Icon name="sparkles" /></span><div><h2>AI 学习助手</h2><p>让每一分钟的内容，都有收获</p></div><span class="ai-status" :class="{ready:status==='已整理完成'}" role="status">{{status}}</span></div>
    <div v-if="needsLogin" class="empty-state auth-gate" role="status"><span class="ai-brand-icon"><Icon name="sparkles" /></span><h3>{{!authReady?'正在恢复登录状态…':authError?'暂时无法确认登录状态':'登录后，读懂这段视频。'}}</h3><p>{{!authReady?'视频解析和下载可照常使用。':authError||'每天可成功生成 3 次总结、获得 10 次问答；失败不计次。'}}</p><button v-if="authReady" class="button primary" @click="authError?restoreSession():emit('login')">{{authError?'重试恢复登录':'登录后生成总结'}}</button></div>
    <template v-else>
    <div class="quota-panel" aria-live="polite"><template v-if="quota"><div class="quota-counters"><span>总结剩余 <b>{{quota.summary.remaining}} / {{quota.summary.limit}}</b><small v-if="quota.summary.reserved"> · {{quota.summary.reserved}} 次处理中</small></span><span>问答剩余 <b>{{quota.chat.remaining}} / {{quota.chat.limit}}</b><small v-if="quota.chat.reserved"> · {{quota.chat.reserved}} 次处理中</small></span></div><p>北京时间 {{resetLabel}} 恢复 · 已保存的结果可免费恢复；首次总结或主动重新总结，完整成功才使用 1 次额度。</p></template><p v-else role="alert">额度暂不可用，已有内容仍可阅读。</p><button class="text-button tiny" @click="refreshQuota">刷新额度</button></div>
    <div class="ai-tabs" role="tablist" aria-label="AI 内容视图"><button v-for="([id,icon,label],index) in tabs" :id="'tab-'+id" ref="tabButtons" :key="id" role="tab" :aria-controls="'panel-'+id" :aria-selected="active===id" :tabindex="active===id?0:-1" @click="switchTab(id)" @keydown="tabKey($event,index)"><Icon :name="icon" />{{label}}</button></div>
    <div class="ai-content">
      <div v-show="active==='summary'" id="panel-summary" role="tabpanel" aria-labelledby="tab-summary">
        <div class="panel-toolbar"><span class="muted tiny"><span class="status-dot"></span>基于视频字幕整理</span><div><button class="icon-button" aria-label="复制摘要" title="复制摘要" :disabled="!summary" @click="copySummary"><Icon name="copy" /></button><button class="text-button tiny" :disabled="loading" @click="startSummarize()"><Icon name="rotate-cw" />重新总结</button></div></div>
        <div v-if="loading&&!summary" class="ai-generation"><div class="loading-caption"><span class="spinner"></span>{{hasSubtitles?'正在生成摘要…':'正在提取字幕，准备整理重点…'}}</div><div class="skeleton"></div><div class="skeleton medium"></div><div class="skeleton"></div><div class="skeleton short"></div></div>
        <template v-if="summary">
          <template v-if="sections.length"><div class="summary-intro"><div class="overline"><Icon name="sparkles" />先读这一段</div><div class="prose summary-prose" v-html="renderMarkdown(sections[0].body)"></div></div><section v-for="(part,index) in sections.slice(1)" :key="index" class="summary-section"><h3><span class="section-num">{{String(index+1).padStart(2,'0')}}</span>{{part.title}}</h3><div class="prose summary-prose" v-html="renderMarkdown(part.body)"></div></section></template>
          <div v-else class="prose summary-prose" v-html="renderMarkdown(summary)"></div>
          <div v-if="loading" class="loading-caption"><span class="spinner"></span>{{mindmap?'正在完成整理…':'正在整理摘要与思维导图…'}}</div>
          <p class="generated-note"><Icon name="info" />{{subtitle?.full_text.length>15000?'本次分析字幕前 15000 字符，重要信息请结合原视频核对。':'AI 基于字幕生成，重要信息请结合原视频核对。'}}</p>
        </template>
        <div v-if="error" :class="summary?'inline-error':'empty-state'" role="alert"><template v-if="!summary"><span class="empty-illustration"><Icon :name="noSubtitles?'captions':'alert-circle'" /></span><h3>{{noSubtitles?'这个视频，还没有可用字幕。':'刚才的思路，暂时中断了。'}}</h3></template><p>{{error}}</p><button class="button outline" style="margin-top:16px" :disabled="loading" @click="restoreFailed?restoreSummary():startSummarize()"><Icon name="rotate-cw" />{{restoreFailed?'重试恢复':'重新生成'}}</button></div>
      </div>
      <div v-show="active==='subtitle'" id="panel-subtitle" role="tabpanel" aria-labelledby="tab-subtitle">
        <div class="subtitle-extension-tools">
          <div v-if="transcription?.subtitle&&subtitle?.has_subtitle" class="subtitle-source-switch"><span class="tiny muted">字幕来源</span><button class="text-button tiny" :disabled="translationBusy" :aria-pressed="sourceKind==='summary'" @click="sourceKind='summary'">总结原字幕</button><button class="text-button tiny" :disabled="translationBusy" :aria-pressed="sourceKind==='transcription'" @click="sourceKind='transcription'">转写字幕</button></div>
          <template v-if="sourceSubtitle?.has_subtitle||matchingTranslation">
            <div class="subtitle-actions"><label for="translation-language" class="tiny">翻译为</label><select id="translation-language" v-model="targetLanguage" :disabled="translationBusy"><option value="zh">中文</option><option value="en">英文</option><option value="ja">日文</option></select><button class="button outline" :disabled="translationBusy||loading||!sourceSubtitle?.has_subtitle" @click="startTranslation(false)"><span v-if="translationBusy" class="spinner"></span>{{translationBusy?'翻译处理中':matchingTranslation?'读取已存译文':'翻译字幕'}}</button><button v-if="matchingTranslation" class="text-button tiny" :disabled="translationBusy||loading" @click="startTranslation(true)">重新翻译（成功计 1 次）</button><button v-if="translationTask" class="text-button tiny" @click="cancelExtension('translation')">取消翻译</button></div>
            <p class="tiny muted">翻译单独计次：{{quota?.translation?`剩余 ${quota.translation.remaining} / ${quota.translation.limit} 次`:'免费 3 次/日，VIP 20 次/日'}}；读取已存译文免费，完整成功才计次。{{translationStatus}}</p>
            <div v-if="matchingTranslation" class="subtitle-source-switch"><button class="text-button tiny" :disabled="!sourceSubtitle?.has_subtitle" :aria-pressed="!showTranslation" @click="showTranslation=false">原文</button><button class="text-button tiny" :aria-pressed="showTranslation" @click="showTranslation=true">译文 · {{targetLanguage}}</button></div>
          </template>
        </div>
        <template v-if="displayedSubtitle?.has_subtitle">
          <div class="subtitle-toolbar"><div><p>共 {{displayedSegments.length}} 条字幕</p><span class="tag">{{subtitleSourceLabel}} · {{displayedSubtitle.language}}</span></div><div class="subtitle-actions"><label class="sr-only" for="subtitle-format">字幕导出格式</label><select id="subtitle-format" v-model="subtitleFormat"><option value="srt">SRT 字幕</option><option value="vtt">VTT 字幕</option><option value="txt">TXT 文本</option></select><button class="button outline" @click="exportSubtitles"><Icon name="download" />下载{{showTranslation&&matchingTranslation?'译文':'字幕'}}</button></div></div>
          <div class="subtitle-list"><div v-for="(segment,index) in visibleSubtitleSegments" :key="index" class="subtitle-row"><span class="time-badge">{{stamp(segment.start)}}</span><span>{{segment.text}}</span></div></div><button v-if="displayedSegments.length>6" class="subtitle-more" @click="expanded=!expanded">{{expanded?'收起字幕':'展开全部 '+displayedSegments.length+' 条字幕'}} <Icon :name="expanded?'chevron-up':'chevron-down'" /></button>
        </template>
        <div v-else class="empty-state"><span v-if="loading" class="spinner"></span><span v-else class="empty-illustration"><Icon name="captions" /></span><h3>{{loading?'正在提取字幕…':'这个视频，还没有字幕。'}}</h3><p>{{loading?'正在获取平台可用的人工或自动字幕。':'可以主动转写音频；转写完成后可直接阅读和导出。'}}</p></div>
        <div v-if="!loading&&(!subtitle?.has_subtitle||transcription)" class="transcription-controls">
          <p class="tiny muted">单视频最多 30 分钟；转写额度独立于总结和问答。{{quota?.transcription?`今日剩余 ${Math.floor(quota.transcription.remaining/60)} / ${Math.floor(quota.transcription.limit/60)} 分钟（${quota.transcription.remaining} 秒）`:'免费每天 60 分钟，VIP 每天 240 分钟'}}。转写成功按实际时长计入额度，失败不扣时长。</p>
          <div class="subtitle-actions"><button class="button outline" :disabled="transcriptionBusy" @click="startTranscription(!!transcription)"><span v-if="transcriptionBusy" class="spinner"></span>{{transcriptionBusy?'转写处理中':transcription?'重新转写（成功扣时长）':'转写音频生成字幕'}}</button><button v-if="transcriptionTask" class="text-button tiny" @click="cancelExtension('transcription')">取消转写</button><button v-if="transcription" class="button outline" :disabled="loading||transcriptionBusy" @click="startSummarize(transcription.id)">用转写字幕生成总结（成功计 1 次）</button></div><p v-if="transcriptionStatus" class="tiny muted" role="status">{{transcriptionStatus}}</p>
        </div>
        <p v-if="extensionError" class="inline-error" role="alert">{{extensionError}}</p>
      </div>
      <div v-show="active==='mindmap'" id="panel-mindmap" role="tabpanel" aria-labelledby="tab-mindmap">
        <MindmapView v-if="mindmap" :markdown="mindmap" :title="videoTitle" :active="active==='mindmap'" @notice="toast" />
        <div v-else class="empty-state"><span v-if="loading" class="spinner"></span><span v-else class="empty-illustration"><Icon name="git-branch" /></span><h3>{{loading?'正在梳理内容…':'思维导图尚未生成。'}}</h3><p>{{loading?'摘要生成后，AI 会把知识整理成导图。':hasSubtitles?'可以切换到总结摘要，重新生成。':'需要视频字幕才能生成导图，下载不受影响。'}}</p></div>
      </div>
      <div v-show="active==='qa'" id="panel-qa" role="tabpanel" aria-labelledby="tab-qa">
        <template v-if="hasSubtitles"><div v-if="!messages.length" class="chat-welcome"><span class="ai-brand-icon"><Icon name="message-circle" /></span><h3>有疑问，就继续问。</h3><p>基于这段视频，和 AI 一起再想深一点。</p><div class="suggestions"><button v-for="text in suggestions" :key="text" class="suggestion" :disabled="chatBusy" @click="sendQuestion(text)">{{text}}<Icon name="arrow-up-right" /></button></div></div><div ref="chatList" class="chat-messages" aria-live="polite"><div v-for="(message,index) in messages" :key="index" class="chat-row" :class="message.role"><span class="avatar"><Icon v-if="message.role==='assistant'" name="sparkles" /><span v-else>你</span></span><div class="chat-bubble"><span v-if="message.role==='user'">{{message.content}}</span><template v-else><span v-if="message.loading&&!message.content"><span class="spinner"></span> 正在思考…</span><div class="prose chat-prose" v-html="renderMarkdown(message.content)"></div><span v-if="message.loading&&message.content" class="stream-cursor" aria-label="正在回答"></span><div v-if="message.error" class="chat-retry">{{message.error}}<button :disabled="chatBusy" @click="retryQuestion(index)">重新回答</button></div></template></div></div></div><form @submit.prevent="sendQuestion()"><label class="chat-input-label" for="chat-input">问问视频里的内容</label><div class="chat-form"><textarea id="chat-input" v-model="question" rows="2" maxlength="1200" placeholder="比如：帮我梳理一下核心观点…" :disabled="chatBusy" @keydown="questionKey"></textarea><button class="button primary" aria-label="发送问题" :disabled="chatBusy||!question.trim()"><Icon name="send" /></button></div><p class="chat-helper">Enter 发送 · Shift + Enter 换行 · 回答仅基于视频字幕与当前对话</p></form></template>
        <div v-else class="empty-state"><span v-if="loading" class="spinner"></span><span v-else class="empty-illustration"><Icon name="message-circle" /></span><h3>{{loading?'正在准备视频内容…':'有了字幕，才能继续问。'}}</h3><p>{{loading?'字幕获取后，你就可以针对视频提问。':'当前没有可用字幕，无法基于视频回答问题。你仍可下载视频。'}}</p></div>
      </div>
    </div>
    </template>
    <div class="ai-footer"><span><Icon name="sparkles" />AI 生成内容仅供参考</span><span>视频解析与下载无需登录</span><button class="text-button tiny" @click="$emit('notice')">查看会员与订单<Icon name="arrow-up-right" /></button></div>
  </section>
  <Teleport to="body"><div v-if="notification" class="toast-stack" role="status" aria-live="polite"><div class="toast"><Icon name="info" /><span>{{notification}}</span><button aria-label="关闭提示" @click="notification=''"><Icon name="x" /></button></div></div></Teleport>
</template>

<style scoped>
.summary-prose,.chat-prose{max-width:none;color:#596982;font-size:16px;line-height:1.85;overflow-wrap:anywhere}
.summary-intro .summary-prose{color:#3b587b}
.summary-prose :deep(p),.chat-prose :deep(p){margin:0 0 12px}
.summary-prose :deep(p:last-child),.chat-prose :deep(p:last-child){margin-bottom:0}
.summary-prose :deep(h1),.summary-prose :deep(h2),.summary-prose :deep(h3){font-size:17px;color:var(--ink);margin:22px 0 12px}
.summary-prose :deep(ul),.summary-prose :deep(ol),.chat-prose :deep(ul),.chat-prose :deep(ol){padding-left:20px;margin:10px 0}
.summary-prose :deep(li),.chat-prose :deep(li){margin:6px 0}
.summary-prose :deep(blockquote),.chat-prose :deep(blockquote){border-left:3px solid #bcd6fc;padding-left:15px;margin:12px 0;font-style:normal;font-weight:400;color:#4f637f}
.summary-prose :deep(pre),.chat-prose :deep(pre){max-width:100%;overflow:auto;background:#1e293b;color:#e2e8f0;padding:14px;border-radius:8px}
.summary-prose :deep(code),.chat-prose :deep(code){font-size:.9em;background:#edf4ff;color:#0961df;padding:2px 4px;border-radius:4px}
.summary-prose :deep(pre code),.chat-prose :deep(pre code){background:none;color:inherit;padding:0}
.summary-prose :deep(table),.chat-prose :deep(table){display:block;max-width:100%;overflow:auto;border-collapse:collapse}
.summary-prose :deep(th),.summary-prose :deep(td),.chat-prose :deep(th),.chat-prose :deep(td){padding:6px 10px;border:1px solid var(--line)}
.summary-prose :deep(a),.chat-prose :deep(a){color:var(--primary);text-decoration:underline}
.chat-bubble{white-space:normal;min-width:0}.chat-row.user .chat-bubble{white-space:pre-wrap}
.stream-cursor{display:inline-block;background:var(--primary);width:2px;height:14px;vertical-align:middle;animation:cursor 1s step-end infinite}
@keyframes cursor{50%{opacity:0}}
.subtitle-extension-tools,.transcription-controls{display:grid;gap:12px;padding:16px 0;border-bottom:1px solid var(--line);margin-bottom:16px}
.subtitle-extension-tools p,.transcription-controls p{margin:0;line-height:1.7}
.subtitle-source-switch{display:flex;flex-wrap:wrap;align-items:center;gap:14px}
.subtitle-source-switch [aria-pressed="true"]{font-weight:700;color:var(--primary);text-decoration:underline;text-underline-offset:5px}
.subtitle-extension-tools .subtitle-actions,.transcription-controls .subtitle-actions{flex-wrap:wrap}
@media(max-width:600px){.subtitle-extension-tools .button,.transcription-controls .button{min-height:44px;white-space:normal}.subtitle-extension-tools select{min-height:44px}}

</style>
