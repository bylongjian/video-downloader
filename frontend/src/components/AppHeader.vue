<script setup>
import { ref } from 'vue'
defineProps({ workbench: Boolean })
defineEmits(['home', 'start', 'notice'])
const open = ref(false)
</script>

<template>
  <div id="site-header">
    <header class="site-header"><div class="container header-inner">
      <a class="brand" href="/" aria-label="SaveAny 首页" @click.prevent="$emit('home')"><img src="/favicon.svg" alt="" width="36" height="36"><span class="brand-name">SaveAny<span class="blue">.</span></span></a>
      <span class="brand-tag">保存视频<br>留住灵感</span>
      <nav class="header-nav" aria-label="主导航">
        <a href="/" :class="{active:!workbench}" @click.prevent="$emit('home')">视频下载</a>
        <a href="/#features">功能亮点</a><a href="/#how-to-use">使用指南</a><a href="/#pricing">会员套餐</a>
      </nav>
      <div class="header-actions"><button class="button ghost login-button" @click="$emit('notice','账号功能将在后续开放，当前视频下载和 AI 功能无需登录。')">登录</button><button class="button primary small" @click="$emit('start')">免费开始<Icon name="arrow-up-right" /></button></div>
      <button class="icon-button mobile-nav-toggle" :aria-label="open?'关闭导航':'打开导航'" :aria-expanded="open" @click="open=!open"><Icon :name="open?'x':'menu'" /></button>
    </div><nav v-if="open" class="mobile-nav" aria-label="手机导航" @click="open=false"><a href="/" @click.prevent="$emit('home')">视频下载</a><a href="/#features">功能亮点</a><a href="/#how-to-use">使用指南</a><a href="/#pricing">会员套餐</a><a href="#" @click.prevent="$emit('notice','账号功能将在后续开放。当前无需登录即可使用。')">登录 · 后续开放</a></nav></header>
  </div>
</template>
