export function saveFile(content, filename, type='text/plain;charset=utf-8') {
  const blob=content instanceof Blob?content:new Blob([content],{type})
  const url=URL.createObjectURL(blob)
  const link=document.createElement('a')
  link.href=url;link.download=filename
  document.body.append(link);link.click();link.remove()
  setTimeout(()=>URL.revokeObjectURL(url),1000)
}

export function safeFilename(title) {
  return (title || '视频').replace(/[\\/:*?"<>|\r\n]/g,'_').slice(0,100)
}

export function subtitleFile(segments, format) {
  if(format==='txt')return segments.map(segment=>segment.text).join('\n')
  const time=seconds=>{
    const millis=Math.round(seconds*1000)
    const hours=Math.floor(millis/3600000)
    const minutes=Math.floor(millis/60000)%60
    const whole=Math.floor(millis/1000)%60
    return `${String(hours).padStart(2,'0')}:${String(minutes).padStart(2,'0')}:${String(whole).padStart(2,'0')}${format==='srt'?',':'.'}${String(millis%1000).padStart(3,'0')}`
  }
  return (format==='vtt'?'WEBVTT\n\n':'')+segments.map((segment,index)=>`${format==='srt'?index+1+'\n':''}${time(segment.start)} --> ${time(segment.end)}\n${segment.text}\n`).join('\n')
}
