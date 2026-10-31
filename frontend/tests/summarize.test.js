import { test } from 'node:test'
import assert from 'node:assert/strict'
import { handleSSEStream, summarizeVideo } from '../src/api/summarize.js'

test('SSE 在 UTF-8、CRLF 和 JSON 换行被分块时仍还原字幕与摘要', async () => {
  const source=': ping\r\nevent: subtitle\r\ndata: {"full_text":\r\ndata: "学习"}\r\n\r\nevent: summary\r\ndata: "第一行\\n第二行"\r\n\r\nevent: done\r\ndata: [DONE]\r\n\r\n'
  const bytes=new TextEncoder().encode(source)
  const response=new Response(new ReadableStream({start(controller){
    for(let index=0;index<bytes.length;index+=3)controller.enqueue(bytes.slice(index,index+3))
    controller.close()
  }}))
  const events=[]
  await handleSSEStream(response,{
    subtitle:data=>events.push(JSON.parse(data)),
    summary:data=>events.push(JSON.parse(data)),
    done:()=>events.push('done'),
  })
  assert.deepEqual(events,[{full_text:'学习'},'第一行\n第二行','done'])
})

test('SSE 中途断流不能当作生成完成', async () => {
  await assert.rejects(
    ()=>handleSSEStream(new Response('event: summary\ndata: "半句话"\n\n'),{}),
    /中断/,
  )
})

test('AI 服务返回非 JSON 网关错误时提示重试而非 JSON 解析异常',async()=>{
  global.fetch=async()=>new Response('Service Unavailable',{status:503})
  await assert.rejects(()=>summarizeVideo('https://example.com/video'),/503.*重试/)
})
