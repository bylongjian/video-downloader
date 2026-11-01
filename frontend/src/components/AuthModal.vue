<script setup>
import {ref,watch,onUnmounted,nextTick} from 'vue'
import {useAuth} from '../composables/useAuth.js'

const props=defineProps({visible:Boolean})
const emit=defineEmits(['close','success'])
const auth=useAuth(),dialog=ref(null),emailInput=ref(null)
const mode=ref('login'),email=ref(''),password=ref(''),showPassword=ref(false),submitting=ref(false),error=ref('')
let opener,attempt=0,controller
watch(()=>props.visible,async visible=>{
  controller?.abort();attempt++;error.value='';password.value='';showPassword.value=false;submitting.value=false
  if(visible){opener=document.activeElement;await nextTick();dialog.value.showModal();emailInput.value.focus()}
  else {dialog.value.close();await nextTick();if(opener?.isConnected)opener.focus()}
})
onUnmounted(()=>{controller?.abort();attempt++})
function close(){controller?.abort();emit('close')}
function switchMode(){mode.value=mode.value==='login'?'register':'login';error.value='';password.value=''}
async function submit(){
  if(submitting.value)return
  const current=++attempt
  controller=new AbortController()
  const signal=controller.signal
  error.value='';submitting.value=true
  try {
    if(new TextEncoder().encode(password.value).length>72)throw new Error('密码编码不能超过 72 字节，请使用较短密码。')
    const success=await auth[mode.value](email.value,password.value,signal)
    if(current===attempt&&success){emit('success');close()}
  } catch(cause){if(current===attempt&&!signal.aborted)error.value=cause.message}
  finally {if(current===attempt)submitting.value=false}
}
</script>

<template>
  <dialog ref="dialog" class="auth-dialog" aria-labelledby="auth-title" @cancel.prevent="close" @click="event=>{if(event.target===dialog)close()}">
    <button class="icon-button dialog-close" aria-label="关闭登录弹窗" @click="close"><Icon name="x" /></button>
    <div class="dialog-inner"><p class="dialog-eyebrow">WELCOME TO SAVEANY</p><h2 id="auth-title">{{mode==='login'?'欢迎回来。':'创建你的账号。'}}</h2><p class="dialog-description">保存好视频，也让知识有处可留。</p>
      <form class="auth-form" @submit.prevent="submit">
        <label for="auth-email">邮箱地址</label><input id="auth-email" ref="emailInput" v-model="email" type="email" autocomplete="username" required maxlength="254" :disabled="submitting" placeholder="your@example.com">
        <label for="auth-password">密码</label><div class="auth-password"><input id="auth-password" v-model="password" :type="showPassword?'text':'password'" :autocomplete="mode==='login'?'current-password':'new-password'" required minlength="8" maxlength="50" :disabled="submitting" aria-describedby="password-help"><button type="button" class="text-button" :aria-pressed="showPassword" :disabled="submitting" @click="showPassword=!showPassword">{{showPassword?'隐藏':'显示'}}</button></div>
        <p id="password-help" class="muted tiny">8～50 个字符，UTF-8 编码不超过 72 字节；密码中的空格会保留。</p>
        <p v-if="error" class="field-error" role="alert">{{error}}</p>
        <button class="button primary full" :disabled="submitting"><span v-if="submitting" class="spinner"></span>{{submitting?'正在提交…':mode==='login'?'登录':'注册并登录'}}</button>
        <p class="auth-switch">{{mode==='login'?'还没有账号？':'已有账号？'}}<button type="button" class="text-button" :disabled="submitting" @click="switchMode">{{mode==='login'?'立即注册':'去登录'}}</button></p>
        <p class="muted tiny">首版暂不支持找回密码，请妥善保存密码。</p>
      </form>
    </div>
  </dialog>
</template>
