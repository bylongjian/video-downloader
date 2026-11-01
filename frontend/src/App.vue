<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import AppHeader from './components/AppHeader.vue'
import AppFooter from './components/AppFooter.vue'
import HeroSection from './components/HeroSection.vue'
import FeatureSection from './components/FeatureSection.vue'
import HowToSection from './components/HowToSection.vue'
import PricingSection from './components/PricingSection.vue'
import FaqSection from './components/FaqSection.vue'
import BottomCta from './components/BottomCta.vue'
import VideoResult from './components/VideoResult.vue'
import VideoSummary from './components/VideoSummary.vue'
import AuthModal from './components/AuthModal.vue'
import {useAuth} from './composables/useAuth.js'
import { parseVideo, downloadVideo } from './api/video.js'

const workbench=ref(location.pathname.startsWith('/workbench'))
const currentUrl=ref(new URLSearchParams(location.search).get('url') || '')
const video=ref(null), loading=ref(false), error=ref('')
const downloading=ref(false), downloadStatus=ref(''), downloadError=ref(''), downloadWarning=ref('')
const hero=ref(null), notice=ref(''), noticeDialog=ref(null)
const auth=useAuth(),authVisible=ref(false),loggingOut=ref(false)
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

async function showNotice(message='VIP 将在后续开放，当前不提供开通。视频解析和下载无需登录。') {
  notice.value=message
  await nextTick()
  if (!noticeDialog.value.open) noticeDialog.value.showModal()
}
function cancel() { parseController?.abort(); downloadController?.abort() }
function home() {
  cancel(); workbench.value=false; video.value=null; loading.value=false
  history.pushState({},'', '/'); document.title='SaveAny · 好视频，值得留下'; window.scrollTo(0,0)
}
async function start() { if(workbench.value) home(); await nextTick(); hero.value.focus() }
async function example() { if(workbench.value) home(); await nextTick(); hero.value.example('bilibili'); window.scrollTo(0,0) }
async function parse(url, navigate=true) {
  cancel(); const controller=new AbortController(); parseController=controller
  currentUrl.value=url; video.value=null; loading.value=true; error.value=''
  downloading.value=false; downloadStatus.value='';downloadError.value='';downloadWarning.value=''
  if(!workbench.value&&navigate) history.pushState({},'', '/workbench?url='+encodeURIComponent(url))
  else if(navigate) history.replaceState({},'', '/workbench?url='+encodeURIComponent(url))
  workbench.value=true; document.title='视频工作台 · SaveAny'; window.scrollTo(0,0)
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
  try {
    const result=await downloadVideo(currentUrl.value,formatId,text=>{if(downloadController===controller)downloadStatus.value=text},controller.signal)
    if(downloadController===controller){downloadStatus.value='文件已交给浏览器保存，请查看浏览器下载列表。';downloadWarning.value=result.warning}
  } catch(cause) {
    if(!controller.signal.aborted&&downloadController===controller){downloadStatus.value='';downloadError.value=cause.message}
  } finally { if(downloadController===controller) downloading.value=false }
}
function popstate() {
  const url=new URLSearchParams(location.search).get('url') || ''
  if(location.pathname.startsWith('/workbench')&&url) parse(url,false)
  else {cancel();workbench.value=false;video.value=null;loading.value=false}
}
onMounted(()=>{auth.restoreSession();window.addEventListener('popstate',popstate);window.addEventListener('focus',focus);if(workbench.value&&currentUrl.value)parse(currentUrl.value,false)})
onUnmounted(()=>{cancel();clearTimeout(quotaTimer);window.removeEventListener('popstate',popstate);window.removeEventListener('focus',focus)})
</script>

<template>
  <a class="skip-link" href="#main">跳到主要内容</a>
  <AppHeader :workbench="workbench" :user="auth.user.value" :auth-ready="auth.authReady.value" :auth-error="auth.authError.value" :logging-out="loggingOut" @login="authVisible=true" @logout="logout" @retry="auth.restoreSession" @home="home" @start="start" @notice="showNotice" />
  <main v-if="!workbench" id="main"><HeroSection ref="hero" @parse="parse" @notice="showNotice" /><FeatureSection @example="example" /><HowToSection /><PricingSection @start="start" @notice="showNotice" /><FaqSection /><BottomCta /></main>
  <main v-else id="main" class="workspace-main"><div class="container">
    <div class="workspace-topline"><a href="/" class="back-link" @click.prevent="home"><Icon name="arrow-left" />返回首页</a><div class="prototype-label"><span class="status-dot"></span>从保存，到理解</div></div>
    <HeroSection ref="hero" compact :initial-url="currentUrl" :loading="loading" @parse="parse" @notice="showNotice" />
    <div class="workspace-heading"><div><p class="kicker">YOUR VIDEO, REIMAGINED</p><h1>把好内容，变成你的知识。</h1></div></div>
    <div v-if="loading" aria-live="polite"><div class="loading-caption"><span class="spinner"></span>正在识别平台并获取视频信息…</div><div class="parse-loading"><div v-for="i in 2" :key="i" class="card skeleton-card"><div class="skeleton" :class="i===1?'cover-skeleton':'short'"></div><div class="skeleton medium"></div><div class="skeleton short"></div><div class="skeleton"></div></div></div></div>
    <div v-else-if="error" class="card empty-state" role="alert"><span class="empty-illustration"><Icon name="link" /></span><h3>这段视频，暂时没找到。</h3><p>{{error}}</p><button class="button primary" @click="parse(currentUrl)"><Icon name="rotate-cw" />重新解析</button><button class="text-button" @click="example">换一个视频</button></div>
    <div v-else-if="video" class="workspace-grid"><VideoResult :key="currentUrl" :video="video" :downloading="downloading" :status="downloadStatus" :error="downloadError" :warning="downloadWarning" @download="download" /><VideoSummary :key="currentUrl+':'+auth.sessionVersion.value" :video-url="currentUrl" :video-title="video.title" @login="authVisible=true" @notice="showNotice" /></div>
    <div v-else class="card empty-state"><h3>从一个视频链接开始。</h3><p>在上方粘贴视频链接，查看视频信息并选择格式下载。</p></div>
  </div></main>
  <AppFooter />
  <AuthModal :visible="authVisible" @close="authVisible=false" />
  <dialog ref="noticeDialog" aria-labelledby="notice-title"><button class="icon-button dialog-close" aria-label="关闭提示" @click="noticeDialog.close()"><Icon name="x" /></button><div class="dialog-inner"><p class="dialog-eyebrow">MORE ROOM TO EXPLORE</p><h2 id="notice-title">留一点期待。</h2><p class="dialog-description notice-description">{{notice}}</p><button class="button primary full" style="margin-top:24px" @click="noticeDialog.close()">继续探索<Icon name="arrow-right" /></button></div></dialog>
</template>
