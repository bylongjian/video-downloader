<script setup>
import {ref,watch,onUnmounted} from 'vue'
import {fetchHistory,deleteHistory} from '../api/history.js'
import {useAuth} from '../composables/useAuth.js'

const props=defineProps({selectedKind:{type:String,default:'summary'}})
const emit=defineEmits(['open','update:selectedKind','close','login','deleted'])
const {user,authReady,authError,sessionVersion,restoreSession}=useAuth()
const activeKind=ref(props.selectedKind),items=ref([]),cursor=ref(null),loading=ref(false),loaded=ref(false),error=ref('')
const confirmId=ref(null),deleting=ref(null),deleteError=ref('')
let listController,deleteController,revision=0
const statuses={started:'已开始下载',browser_handoff:'已交给浏览器处理',server_ready:'服务端文件已准备',expired:'文件已过期',failed:'下载失败',cancelled:'已取消',cancelling:'正在取消',succeeded:'已完成',queued:'等待处理',running:'处理中',interrupted:'已中断'}
function date(value){return new Date(value).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false})}
function selectKind(kind){activeKind.value=kind;emit('update:selectedKind',kind)}
async function load(more=false) {
  if(!authReady.value||!user.value||loading.value)return
  listController?.abort()
  const controller=new AbortController(),current=revision,version=sessionVersion.value
  listController=controller;loading.value=true;error.value=''
  try {
    const result=await fetchHistory(activeKind.value,more?cursor.value:null,controller.signal)
    if(current!==revision||version!==sessionVersion.value||controller.signal.aborted)return
    items.value=more?[...items.value,...result.items]:result.items
    cursor.value=result.next_cursor;loaded.value=true
  } catch(cause) {
    if(current===revision&&version===sessionVersion.value&&!controller.signal.aborted)error.value=cause.message
  } finally {if(current===revision&&version===sessionVersion.value)loading.value=false}
}
function reset() {
  revision++;listController?.abort();deleteController?.abort()
  items.value=[];cursor.value=null;error.value='';deleteError.value='';loaded.value=false;loading.value=false;confirmId.value=null;deleting.value=null
  load()
}
async function remove(item) {
  if(deleting.value)return
  const controller=new AbortController(),current=revision,version=sessionVersion.value
  deleteController=controller;deleting.value=item.id;deleteError.value=''
  try {
    await deleteHistory(activeKind.value,item.id,controller.signal)
    if(current!==revision||version!==sessionVersion.value||controller.signal.aborted)return
    items.value=items.value.filter(entry=>entry.id!==item.id);confirmId.value=null
    emit('deleted',activeKind.value,item.id)
  } catch(cause) {
    if(current===revision&&version===sessionVersion.value&&!controller.signal.aborted)deleteError.value=cause.message
  } finally {if(current===revision&&version===sessionVersion.value)deleting.value=null}
}
watch(()=>props.selectedKind,kind=>{activeKind.value=kind})
watch([activeKind,authReady,user,sessionVersion],reset,{immediate:true,flush:'sync'})
onUnmounted(()=>{revision++;listController?.abort();deleteController?.abort()})
</script>

<template>
  <section class="card history-card" aria-labelledby="history-title">
    <div class="history-heading"><div><p class="muted tiny">只对当前账号可见</p><h1 id="history-title">我的历史</h1></div><button class="button ghost" @click="emit('close')"><Icon name="arrow-left" />返回下载</button></div>
    <div v-if="!authReady||!user" class="empty-state" role="status"><Icon name="lock" /><h2>{{!authReady?'正在恢复登录…':authError?'暂时无法确认登录状态':'登录后查看你的历史'}}</h2><p>{{authError||'成功的总结和下载记录会保存在当前账号。'}}</p><button v-if="authReady" class="button primary" @click="authError?restoreSession():emit('login')">{{authError?'重试恢复登录':'登录 / 注册'}}</button></div>
    <template v-else>
      <nav class="history-tabs" aria-label="历史类型"><button v-for="kind in ['summary','download']" :key="kind" class="button" :class="activeKind===kind?'soft':'ghost'" :aria-pressed="activeKind===kind" @click="selectKind(kind)"><Icon :name="kind==='summary'?'file-text':'download'" />{{kind==='summary'?'总结历史':'下载历史'}}</button></nav>
      <p class="history-hint muted">{{activeKind==='summary'?'打开已保存的摘要、字幕和导图，不会重新生成或扣次。':'记录只反映下载请求和浏览器处理状态，无法确认文件已保存到本机。'}}</p>
      <div v-if="error" class="history-error inline-error" role="alert"><p>{{error}}</p><button class="text-button" :disabled="loading" @click="load(loaded)">重试加载</button></div>
      <div v-if="loading&&!items.length" class="empty-state" role="status"><span class="spinner"></span><p>正在读取历史…</p></div>
      <div v-else-if="loaded&&!items.length&&!error" class="empty-state"><Icon :name="activeKind==='summary'?'file-text':'download'" /><h2>{{activeKind==='summary'?'还没有保存的总结':'还没有下载记录'}}</h2><p>{{activeKind==='summary'?'成功完成一次总结后，就可以在这里再次阅读。':'登录后开始下载，记录会出现在这里。'}}</p></div>
      <ul v-if="items.length" class="history-list" :aria-busy="loading">
        <li v-for="item in items" :key="item.id" class="history-item">
          <div class="history-item-heading"><span class="platform-badge">{{item.platform||'视频'}}</span><span class="tag">{{item.kind==='summary'?'已保存':statuses[item.status]||'状态未知'}}</span><time :datetime="item.updated_at" class="muted tiny">{{date(item.updated_at)}}</time></div>
          <h2><button class="history-title" @click="emit('open',activeKind,item.id)">{{item.video_title||item.url}}</button></h2>
          <p class="history-url muted tiny">{{item.url}}</p>
          <div class="history-item-actions"><button class="text-button" @click="emit('open',activeKind,item.id)">{{activeKind==='summary'?'打开保存结果':'查看下载记录'}}<Icon name="arrow-right" /></button><button class="text-button history-delete" :disabled="!!deleting" @click="confirmId=item.id;deleteError=''">删除</button></div>
          <div v-if="confirmId===item.id" class="history-confirm" role="group" :aria-label="'确认删除'+(activeKind==='summary'?'总结':'下载记录')">
            <p>{{activeKind==='summary'?'删除后，保存的摘要、字幕、导图及免费恢复入口都会移除，已使用次数不会退回。之后重新总结会按正常规则计次。':'只删除这条历史记录，不影响你已保存到本机的文件。'}}</p>
            <p v-if="deleteError" class="inline-error" role="alert">{{deleteError}}</p>
            <div><button class="button outline" :disabled="!!deleting" @click="confirmId=null">保留记录</button><button class="button outline history-delete" :disabled="!!deleting" @click="remove(item)">{{deleting===item.id?'正在删除…':'确认删除'}}</button></div>
          </div>
        </li>
      </ul>
      <div v-if="cursor&&items.length" class="history-more"><button class="button outline" :disabled="loading" @click="load(true)"><span v-if="loading" class="spinner"></span>{{loading?'正在加载…':'加载更多（每页 20 条）'}}</button></div>
    </template>
  </section>
</template>

<style scoped>
.history-card{padding:32px;min-width:0}.history-heading{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:26px}.history-heading h1{font-size:30px;margin-top:6px}.history-tabs{display:flex;gap:12px;flex-wrap:wrap}.history-hint{font-size:15px;margin:18px 0 24px}.history-list{list-style:none;padding:0;margin:0}.history-item{border-top:1px solid var(--line);padding:24px 0}.history-item-heading{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.history-item-heading time{margin-left:auto}.history-item h2{font-size:20px;margin-top:16px}.history-title{padding:0;border:0;background:none;text-align:left;font:inherit;line-height:1.5;overflow-wrap:anywhere}.history-title:hover{color:var(--primary-dark)}.history-url{overflow-wrap:anywhere;margin-top:8px}.history-item-actions{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-top:12px}.history-delete{color:var(--error)}.history-confirm{padding:18px;margin-top:14px;background:var(--surface);border:1px solid var(--line);border-radius:12px;font-size:15px}.history-confirm>div{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:12px;margin-top:14px}.history-error{padding:18px 0}.history-more{text-align:center;margin-top:20px}.empty-state h2{font-size:22px}.empty-state>.button{margin-top:20px}
@media(max-width:640px){.history-card{padding:22px 20px}.history-heading{align-items:flex-start;gap:8px}.history-heading h1{font-size:26px}.history-heading>.button{padding-inline:10px}.history-item-heading time{margin-left:0;width:100%;font-size:14px}.history-confirm{padding:14px}.history-confirm>div{justify-content:flex-start}.history-tabs{gap:8px}}
</style>
