<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { Transformer } from 'markmap-lib'
import { Markmap } from 'markmap-view'
import DOMPurify from 'dompurify'
import { saveFile, safeFilename } from '../downloads.js'

const props=defineProps({markdown:{type:String,required:true},title:String,active:Boolean})
const emit=defineEmits(['notice'])
const canvas=ref(null),wrap=ref(null),percent=ref(100),full=ref(false),exporting=ref(false),error=ref('')
let instance,observer,resizeObserver,disposed=false
const colors=['#1777ff','#3185df','#25a080','#8c70cf','#d39a36']

function clean(node,depth=0,color=colors[0]) {
  const content=DOMPurify.sanitize(node.content,{ALLOWED_TAGS:['strong','em','code','br'],ALLOWED_ATTR:[]})
  node.content=`<span class="map-level-${Math.min(depth,2)}" style="border-color:${color}">${content}</span>`
  node.payload={...node.payload,branchColor:color}
  node.children?.forEach((child,index)=>clean(child,depth+1,depth===0?colors[index%4+1]:color))
}
async function render() {
  await nextTick()
  if(disposed||!props.markdown||!props.active)return
  error.value=''
  try {
    const {root}=new Transformer().transform(props.markdown)
    clean(root)
    if(!instance) {
      instance=Markmap.create(canvas.value,{
        duration:0,maxWidth:240,autoFit:false,fitRatio:0.9,
        color:node=>node.payload.branchColor,
      })
      observer=new MutationObserver(()=>{
        const scale=instance.g.attr('transform')?.match(/scale\(([-\d.e]+)\)/)
        percent.value=Math.round(Number(scale?.[1] || 1)*100)
      })
      observer.observe(instance.g.node(),{attributes:true,attributeFilter:['transform']})
      resizeObserver=new ResizeObserver(()=>{if(props.active)fitMap()})
      resizeObserver.observe(canvas.value)
    }
    await instance.setData(root)
    instance.g.selectAll('.markmap-node').attr('tabindex',0).attr('role','button').attr('aria-label',node=>'展开或折叠 '+node.content.replace(/<[^>]+>/g,''))
      .on('keydown.keyboard',(event,node)=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();instance.toggleNode(node)}})
    await fitMap()
  } catch(cause) {error.value='思维导图暂时无法显示，请重新总结。'}
}
async function fitMap() {
  await nextTick()
  if(disposed||!instance||!props.active)return
  // 隐藏标签的节点尺寸会变成 0；先按可见内容重新排版，再适应画布。
  await instance.renderData()
  await instance.fit()
}
function zoom(scale) {instance?.rescale(scale)}
function pan(x,y) {if(instance)instance.svg.call(instance.zoom.translateBy,x,y)}
async function fullscreen() {
  try {if(document.fullscreenElement)await document.exitFullscreen();else await wrap.value.requestFullscreen()}
  catch {emit('notice','当前浏览器不支持全屏，请使用缩放和定位按钮查看。')}
}
async function fullscreenChanged() {full.value=document.fullscreenElement===wrap.value;await fitMap()}

function exportSvg() {
  const original=canvas.value, cloned=original.cloneNode(true)
  const bounds=instance.g.node().getBBox()
  const padding=36,width=Math.ceil(bounds.width+padding*2),height=Math.ceil(bounds.height+padding*2)
  const root=cloned.querySelector('g')
  root.removeAttribute('transform')
  cloned.removeAttribute('class');cloned.removeAttribute('style')
  cloned.setAttribute('xmlns','http://www.w3.org/2000/svg')
  cloned.setAttribute('viewBox',`${bounds.x-padding} ${bounds.y-padding} ${width} ${height}`)
  cloned.setAttribute('width',width);cloned.setAttribute('height',height)
  const originalShapes=original.querySelectorAll('path,line,circle,rect')
  cloned.querySelectorAll('path,line,circle,rect').forEach((shape,index)=>{
    const style=getComputedStyle(originalShapes[index])
    for(const name of ['fill','stroke','stroke-width'])shape.setAttribute(name,style.getPropertyValue(name))
  })
  const sourceObjects=original.querySelectorAll('foreignObject')
  const measure=document.createElement('canvas').getContext('2d')
  cloned.querySelectorAll('foreignObject').forEach((object,index)=>{
    const source=sourceObjects[index]
    const style=getComputedStyle(source.querySelector('[class^="map-level-"]') || source)
    const size=parseFloat(style.fontSize)||14,lineHeight=parseFloat(style.lineHeight)||size*1.4
    const x=Number(object.getAttribute('x') || 0),y=Number(object.getAttribute('y') || 0)
    const left=parseFloat(style.paddingLeft)||0,top=parseFloat(style.paddingTop)||0
    const available=Number(object.getAttribute('width'))-left-(parseFloat(style.paddingRight)||0)+2
    measure.font=`${style.fontWeight} ${size}px ${style.fontFamily}`
    const lines=[]
    let line=''
    for(const letter of source.textContent.trim()) {
      if(letter==='\n'||(line&&measure.measureText(line+letter).width>available)){lines.push(line);line=letter==='\n'?'':letter}
      else line+=letter
    }
    if(line)lines.push(line)
    const text=document.createElementNS('http://www.w3.org/2000/svg','text')
    text.setAttribute('x',x+left+2);text.setAttribute('y',y+top+size)
    text.setAttribute('font-family',style.fontFamily);text.setAttribute('font-size',size)
    text.setAttribute('font-weight',style.fontWeight);text.setAttribute('fill',style.color)
    lines.forEach((value,lineIndex)=>{
      const span=document.createElementNS('http://www.w3.org/2000/svg','tspan')
      span.setAttribute('x',x+left+2);span.setAttribute('dy',lineIndex?lineHeight:0);span.textContent=value;text.append(span)
    })
    const group=document.createElementNS('http://www.w3.org/2000/svg','g')
    if(style.backgroundColor!=='rgba(0, 0, 0, 0)') {
      const box=document.createElementNS('http://www.w3.org/2000/svg','rect')
      for(const [key,value] of Object.entries({x,y,width:object.getAttribute('width'),height:object.getAttribute('height'),rx:parseFloat(style.borderRadius)||0,fill:style.backgroundColor,stroke:parseFloat(style.borderWidth)?style.borderColor:'none'}))box.setAttribute(key,value)
      group.append(box)
    }
    group.append(text);object.replaceWith(group)
  })
  const background=document.createElementNS('http://www.w3.org/2000/svg','rect')
  for(const [key,value] of Object.entries({x:bounds.x-padding,y:bounds.y-padding,width,height,fill:'#ffffff'}))background.setAttribute(key,value)
  cloned.insertBefore(background,cloned.firstChild)
  return {text:new XMLSerializer().serializeToString(cloned),width,height}
}
async function download(format) {
  if(!instance||exporting.value)return
  exporting.value=true
  try {
    const svg=exportSvg(),filename=safeFilename(props.title)+' - 思维导图'
    if(format==='svg'){saveFile(svg.text,filename+'.svg','image/svg+xml;charset=utf-8');emit('notice','已导出完整 SVG 思维导图');return}
    const scale=Math.max(4,4096/svg.width)
    const image=new Image(),url=URL.createObjectURL(new Blob([svg.text],{type:'image/svg+xml;charset=utf-8'}))
    try {
      await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=reject;image.src=url})
      const output=document.createElement('canvas')
      output.width=Math.ceil(svg.width*scale);output.height=Math.ceil(svg.height*scale)
      output.getContext('2d').drawImage(image,0,0,output.width,output.height)
      const png=await new Promise(resolve=>output.toBlob(resolve,'image/png'))
      if(!png)throw new Error('PNG export failed')
      saveFile(png,filename+'.png')
      emit('notice',`已导出 ${output.width} × ${output.height} 高清 PNG`)
    } finally {URL.revokeObjectURL(url)}
  } catch {emit('notice','导图导出失败，请重试或使用 SVG 格式。')}
  finally {exporting.value=false}
}
watch(()=>props.markdown,render)
watch(()=>props.active,active=>{if(active){if(instance)fitMap();else render()}})
onMounted(()=>{document.addEventListener('fullscreenchange',fullscreenChanged);render()})
onUnmounted(()=>{disposed=true;observer?.disconnect();resizeObserver?.disconnect();instance?.destroy();document.removeEventListener('fullscreenchange',fullscreenChanged)})
</script>

<template>
  <div class="map-toolbar"><span class="muted">把内容，连成一张图。</span><div class="map-actions"><button class="button outline" :disabled="exporting||!!error" @click="download('png')"><Icon name="download" />PNG</button><button class="button outline" :disabled="exporting||!!error" @click="download('svg')">SVG</button><button class="icon-button" aria-label="全屏思维导图" title="全屏" @click="fullscreen"><Icon name="expand" /></button></div></div>
  <div v-if="error" class="inline-error" role="alert">{{error}}</div>
  <div ref="wrap" class="map-wrap"><button v-if="full" class="button outline small map-exit" style="display:inline-flex" @click="fullscreen"><Icon name="x" />退出全屏</button><svg ref="canvas" class="map-canvas" aria-label="视频内容思维导图"></svg><div class="map-nav"><button class="icon-button" aria-label="缩小导图" @click="zoom(1/1.2)"><Icon name="minus" /></button><span id="map-percent" class="tiny muted">{{percent}}%</span><button class="icon-button" aria-label="放大导图" @click="zoom(1.2)"><Icon name="plus" /></button><button class="icon-button" aria-label="导图向左平移" @click="pan(-35,0)"><Icon name="chevron-left" /></button><button class="icon-button" aria-label="导图向右平移" @click="pan(35,0)"><Icon name="chevron-right" /></button><button class="icon-button" aria-label="导图向上平移" @click="pan(0,-35)"><Icon name="chevron-up" /></button><button class="icon-button" aria-label="导图向下平移" @click="pan(0,35)"><Icon name="chevron-down" /></button><button class="icon-button" aria-label="导图适应画布" @click="fitMap"><Icon name="rotate-cw" /></button></div></div>
  <p class="map-help">点击节点展开 / 折叠 · 拖动空白处平移 · 使用按钮缩放与定位</p>
</template>

<style scoped>
.map-canvas :deep(.markmap-foreign){font:400 16px/24px var(--font);color:#52647c}
.map-canvas :deep(.map-level-0){display:inline-block;background:#1777ff;color:#fff;padding:12px 18px;border-radius:12px;font:600 18px/28px var(--font)}
.map-canvas :deep(.map-level-1){display:inline-block;background:#fff;color:#3a4d65;border:1px solid;padding:7px 12px;border-radius:8px;font:400 16px/24px var(--font)}
.map-canvas :deep(.map-level-2){font:400 14px/22px var(--font);color:#5f7187}
.map-canvas :deep(foreignObject){overflow:visible}
.map-canvas :deep(.markmap-node:focus-visible){outline:2px solid #1777ff;outline-offset:3px}
.map-wrap:fullscreen .map-canvas{flex:1;height:calc(100dvh - 130px)}
</style>
