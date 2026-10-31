<script setup>
import { ref, watch, nextTick } from 'vue'
import PlatformSection from './PlatformSection.vue'
const props = defineProps({ compact: Boolean, loading: Boolean, initialUrl: {type:String,default:''} })
const emit = defineEmits(['parse', 'notice'])
const url = ref(props.initialUrl)
const input = ref(null)
const error = ref('')
const examples = {bilibili:'https://www.bilibili.com/video/BV1mAAmzqEfP',youtube:'https://www.youtube.com/watch?v=aqz-KE-bpKQ'}
watch(() => props.initialUrl, value => { url.value=value })
watch(url, () => { error.value='' })
async function focus() { await nextTick(); input.value?.focus() }
function example(name) { url.value=examples[name]; focus() }
async function paste() {
  try { url.value=await navigator.clipboard.readText() }
  catch { emit('notice','请在输入框中使用 Ctrl / ⌘ + V 粘贴链接。') }
  focus()
}
function submit() {
  const match=url.value.trim().match(/https?:\/\/[^\s<>]+/i)
  const value=(match?match[0]:url.value.trim()).replace(/[，。！）)]+$/, '')
  let parsed
  try { parsed=new URL(value) } catch { error.value='请输入完整的视频链接，例如 https://www.bilibili.com/video/…'; focus(); return }
  if (!['https:','http:'].includes(parsed.protocol)) { error.value='请使用 http 或 https 开头的视频链接。'; return }
  url.value=value
  emit('parse',value)
}
defineExpose({focus,example})
</script>

<template>
  <component :is="compact?'div':'section'" :class="compact?'':'hero'">
    <div v-if="!compact" class="hero-grid" aria-hidden="true"></div>
    <div :class="compact?'':'container hero-content'">
      <template v-if="!compact"><div class="eyebrow"><span class="status-dot"></span>一个链接，从保存到理解 <span class="eyebrow-arrow">↗</span></div><h1>好视频，值得<span class="blue">留下。</span><br>好内容，轻松<span class="hero-mark">读懂。</span></h1><p class="hero-description">下载你喜欢的视频，让 AI 帮你提炼重点。<br class="mobile-only">从收藏到消化，只差一个链接。</p></template>
      <form class="url-form" :class="{'compact-form':compact}" novalidate @submit.prevent="submit">
        <label class="input-label" for="video-url">{{compact?'换一个视频，继续探索':'视频链接'}}</label>
        <div class="url-field"><span><Icon name="link" /></span><input id="video-url" ref="input" v-model="url" name="url" type="url" autocomplete="url" :placeholder="compact?'粘贴新的视频链接…':'粘贴视频链接，开始探索…'" :aria-invalid="!!error" aria-describedby="url-error"><button v-if="url" type="button" class="icon-button" aria-label="清空链接" @click="url='';focus()"><Icon name="x" /></button><button type="button" class="paste-button" @click="paste"><Icon name="clipboard" /><span>粘贴</span></button><button class="button primary" type="submit" :disabled="loading"><span v-if="loading" class="spinner"></span><Icon v-else name="sparkles" />{{loading?'解析中…':'解析视频'}}<Icon v-if="!compact" name="arrow-right" /></button></div>
        <p v-if="error" id="url-error" class="field-error" role="alert">{{error}}</p>
      </form>
      <template v-if="!compact"><div class="try-links"><span>还没想好？试试</span><button @click="example('bilibili')"><span class="platform-letter bili">B</span>B 站学习教程</button><button @click="example('youtube')"><span class="platform-letter youtube">▶</span>YouTube 创意短片</button></div><div class="hero-assurances"><span><Icon name="check-circle" />无需安装</span><span><Icon name="check-circle" />多种清晰度</span><span><Icon name="check-circle" />手机也能用</span></div><PlatformSection /></template>
    </div>
  </component>
</template>
