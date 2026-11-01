<script setup>
import { ref } from 'vue'
defineProps({workbench:Boolean,user:Object,authReady:Boolean,authError:String,loggingOut:Boolean})
defineEmits(['home','start','notice','login','logout','retry'])
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
      <div class="header-actions"><span v-if="!authReady" class="muted tiny" role="status">恢复登录中…</span><template v-else-if="user"><span class="account-email" :title="user.email">{{user.email}}</span><button class="button ghost" :disabled="loggingOut" @click="$emit('logout')">{{loggingOut?'退出中…':'退出'}}</button></template><button v-else-if="authError" class="text-button tiny" @click="$emit('retry')">重试登录状态</button><button v-else class="button ghost login-button" @click="$emit('login')">登录</button><button class="button primary small" @click="$emit('start')">免费开始<Icon name="arrow-up-right" /></button></div>
      <button class="icon-button mobile-nav-toggle" :aria-label="open?'关闭导航':'打开导航'" :aria-expanded="open" @click="open=!open"><Icon :name="open?'x':'menu'" /></button>
    </div><nav v-if="open" class="mobile-nav" aria-label="手机导航" @click="open=false"><a href="/" @click.prevent="$emit('home')">视频下载</a><a href="/#features">功能亮点</a><a href="/#how-to-use">使用指南</a><a href="/#pricing">会员套餐</a><template v-if="user"><span class="account-email">{{user.email}}</span><button class="text-button" :disabled="loggingOut" @click="$emit('logout')">退出账号</button></template><button v-else-if="authReady" class="text-button" @click="$emit(authError?'retry':'login')">{{authError?'重试登录状态':'登录 / 注册'}}</button></nav></header>
  </div>
</template>
