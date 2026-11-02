<script setup>
import { computed, ref } from 'vue'
const props=defineProps({video:{type:Object,required:true},downloading:Boolean,queueing:Boolean,status:String,error:String,warning:String})
defineEmits(['download','background-download'])
const selected=ref(props.video.formats[0]?.format_id || '')
const coverFailed=ref(false)
const format=computed(()=>props.video.formats.find(item=>item.format_id===selected.value))
const cover=computed(()=>'/api/proxy/thumbnail?url='+encodeURIComponent(props.video.thumbnail))
function size(bytes) { return bytes?bytes>=1073741824?(bytes/1073741824).toFixed(2)+' GB':(bytes/1048576).toFixed(1)+' MB':'未知大小' }
function quality(item) { return item.height?item.height+'p':item.vcodec==='none'?'仅音频':'原始画质' }
function views(count) { return count==null?'':count>=10000?(count/10000).toFixed(1)+' 万次播放':count.toLocaleString()+' 次播放' }
const date=computed(()=>props.video.upload_date?.replace(/^(\d{4})(\d{2})(\d{2})$/,'$1-$2-$3'))
</script>

<template>
  <aside class="video-column">
    <article class="card video-card">
      <div class="video-cover">
        <img v-if="video.thumbnail&&!coverFailed" :src="cover" :alt="video.title" referrerpolicy="no-referrer" @error="coverFailed=true">
        <div v-else class="muted" style="margin:auto;text-align:center;color:#c8d8ef"><Icon name="play" style="width:44px;height:44px" /><p style="font-size:12px;margin-top:12px">{{coverFailed?'封面暂不可用':'暂无视频封面'}}</p></div>
        <span class="duration">{{video.duration==null?'时长未知':video.duration_string}}</span>
      </div>
      <div class="video-card-body">
        <div class="video-meta-top"><span class="platform-badge">{{video.platform}}</span><span class="tiny muted">{{date}}</span></div>
        <h2>{{video.title}}</h2>
        <div class="creator-line"><span class="creator-avatar">{{video.uploader?.slice(0,1) || '视'}}</span><span>{{video.uploader}}</span><template v-if="video.view_count!=null"><span class="meta-separator">·</span><span>{{views(video.view_count)}}</span></template></div>
        <p class="video-description">{{video.description || '选择可用画质与格式，保存这段视频。'}}</p>
        <div class="format-heading"><h3><Icon name="sliders" />下载设置</h3><span v-if="format" class="tag">{{format.vcodec==='none'?'音频':format.acodec==='none'?'仅视频':format.acodec?'含音频':'音轨信息未知'}}</span></div>
        <div class="format-label">选择画质与格式</div>
        <div class="format-options" role="radiogroup" aria-label="画质与格式">
          <label v-for="(item,index) in video.formats" :key="item.format_id" class="format-option" :title="item.label"><input v-model="selected" type="radio" name="format" :value="item.format_id" :disabled="downloading"><div><strong>{{quality(item)}}</strong><span class="format-ext">{{item.ext.toUpperCase()}}</span><span v-if="index===0" class="recommended">推荐</span></div><span class="format-size">{{size(item.filesize || item.filesize_approx)}}</span></label>
        </div>
        <p v-if="!video.formats.length" class="inline-error">该视频暂无可下载格式，请尝试其他视频。</p>
        <button class="button primary full download-button" :disabled="!selected||downloading" @click="$emit('download',selected)"><span v-if="downloading" class="spinner"></span><Icon v-else name="download" /><span>{{downloading?'正在下载…':'下载视频'}}</span><span v-if="format">{{size(format.filesize || format.filesize_approx)}}</span></button>
        <button class="button outline full" :disabled="!selected||queueing" @click="$emit('background-download',selected)">{{queueing?'正在加入队列…':'后台下载 · 查看实时进度'}}</button>
        <div class="download-status" aria-live="polite"><p v-if="status">{{status}}</p><p v-if="warning" class="inline-error">{{warning}}</p><div v-if="error" class="inline-error" role="alert">{{error}}<button class="text-button tiny" @click="$emit('download',selected)">重新下载</button></div></div>
        <p class="download-hint"><Icon name="shield-check" />自动选择合适的下载方式</p>
      </div>
    </article>
    <div class="workspace-tip"><Icon name="info" /><p>留住好内容，也尊重好创作。<br>请仅下载你拥有版权或已获授权的视频。</p></div>
  </aside>
</template>

<style scoped>
.video-card h2,.video-description,.creator-line{overflow-wrap:anywhere}
</style>
