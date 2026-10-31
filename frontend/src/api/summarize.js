export async function handleSSEStream(response, callbacks) {
  const reader=response.body.getReader()
  const decoder=new TextDecoder()
  let buffer='', event='', data=[], terminal=false
  function line(value) {
    if(value==='') {
      if(data.length) {
        const name=event || 'message', payload=data.join('\n')
        if(name==='error'&&!callbacks.error) throw new Error(JSON.parse(payload).message)
        callbacks[name]?.(payload)
        if(name==='done'||name==='error')terminal=true
      }
      event='';data=[]
      return
    }
    if(value.startsWith(':')) return
    const colon=value.indexOf(':')
    const field=colon<0?value:value.slice(0,colon)
    const content=colon<0?'':value.slice(colon+1).replace(/^ /,'')
    if(field==='event')event=content
    else if(field==='data')data.push(content)
  }
  try {
    while(!terminal) {
      const {done,value}=await reader.read()
      buffer+=done?decoder.decode():decoder.decode(value,{stream:true})
      while(!terminal) {
        const end=buffer.search(/[\r\n]/)
        if(end<0 || (!done&&buffer[end]==='\r'&&end===buffer.length-1))break
        const length=buffer[end]==='\r'&&buffer[end+1]==='\n'?2:1
        line(buffer.slice(0,end));buffer=buffer.slice(end+length)
      }
      if(done)break
    }
    if(!terminal)throw new Error('AI 响应中断，请重试。已收到的内容会保留。')
  } finally {
    await reader.cancel().catch(()=>{})
    reader.releaseLock()
  }
}

async function stream(path, body, callbacks, signal) {
  const response=await fetch(`/api/${path}`,{
    method:'POST',headers:{'Content-Type':'application/json',Accept:'text/event-stream'},
    body:JSON.stringify(body),signal,
  })
  if(!response.ok) {
    let result
    try {result=await response.json()}
    catch {throw new Error(`AI 服务响应异常（HTTP ${response.status}），请稍后重试。`)}
    throw new Error(result.error || result.detail || `请求失败（${response.status}）`)
  }
  if(!response.headers.get('content-type')?.includes('text/event-stream'))throw new Error('AI 服务未返回有效事件流，请重试。')
  await handleSSEStream(response,callbacks)
}

export function summarizeVideo(url,language='zh',callbacks={},signal) {
  return stream('summarize',{url,language},callbacks,signal)
}

export function chatWithVideo(url,question,subtitleText='',callbacks={},signal,history=[]) {
  return stream('chat',{url,question,subtitle_text:subtitleText,history},callbacks,signal)
}
