<script setup>
import HomeContent from './components/HomeContent.vue'
import AppFooter from './components/AppFooter.vue'
import GuidePage from './components/GuidePage.vue'
defineProps({page:{type:String,required:true}})
</script>

<template>
  <a class="skip-link" href="#main">跳到主要内容</a>
  <div id="site-header"><header class="site-header"><div class="container header-inner">
    <a class="brand" href="/" aria-label="SaveAny 首页"><img src="/favicon.svg" alt="" width="36" height="36"><span class="brand-name">SaveAny<span class="blue">.</span></span></a>
    <span class="brand-tag">保存视频<br>留住灵感</span>
    <nav class="header-nav" aria-label="主导航"><a href="/">视频下载</a><a href="/queue">批量任务</a><a href="/guide/">使用指南</a><a href="/billing">会员与订单</a></nav>
    <a class="button primary small" href="/#main">开始解析</a>
  </div></header></div>
  <main v-if="page==='home'" id="main"><HomeContent /></main>
  <main v-else-if="page==='guide'" id="main"><GuidePage /></main>
  <main v-else id="main" class="workspace-main"><div class="container"><section class="card empty-state"><h1>404 · 页面未找到</h1><p>这个页面不存在，返回首页继续解析视频。</p><a class="button primary" href="/">返回 SaveAny 首页</a></section></div></main>
  <AppFooter />
  <noscript><p class="container">产品介绍可以直接阅读；解析、下载和账号功能需要启用浏览器 JavaScript。</p></noscript>
</template>
