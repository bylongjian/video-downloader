import { test } from 'node:test'
import assert from 'node:assert/strict'
import { downloadVideo, parseVideo } from '../src/api/video.js'

function browserBoundary() {
  const clicks = []
  global.document = {
    body: { append() {} },
    createElement: () => ({ click() { clicks.push(this.download) }, remove() {} }),
  }
  return clicks
}

test('可用直链保存实际响应，不请求代理下载', async () => {
  const clicks = browserBoundary()
  const calls = []
  global.fetch = async (url) => {
    calls.push(url)
    if (url === '/api/direct-url') return Response.json({ success: true, data: {
      direct_url: 'https://media.example/video.mp4', ext: 'mp4', title: '示例',
    } })
    return new Response('actual video bytes', { headers: {
      'Content-Type': 'video/mp4',
      'Content-Disposition': 'attachment; filename="lesson;part 1.mp4"',
    } })
  }
  const result = await downloadVideo('https://example.com/video', '18', () => {})
  assert.equal(result.mode, 'direct')
  assert.deepEqual(calls, ['/api/direct-url', 'https://media.example/video.mp4'])
  assert.deepEqual(clicks, ['lesson;part 1.mp4'])
})

test('直链被浏览器拒绝时回退代理并使用服务端文件名', async () => {
  const clicks = browserBoundary()
  const calls = []
  global.fetch = async (url) => {
    calls.push(url)
    if (url === '/api/direct-url') return Response.json({ success: true, data: {
      direct_url: 'https://media.example/video.mp4', ext: 'mp4', title: '视频',
    } })
    if (url.startsWith('https:')) throw new TypeError('Failed to fetch')
    return new Response('proxy video bytes', { headers: {
      'Content-Type': 'video/mp4',
      'Content-Disposition': "attachment; filename*=UTF-8''%E6%95%99%E7%A8%8B.mp4",
    } })
  }
  const result = await downloadVideo('https://example.com/video', '18', () => {})
  assert.equal(result.mode, 'proxy')
  assert.equal(calls.at(-1), '/api/download')
  assert.deepEqual(clicks, ['教程.mp4'])
})

test('后端不可用时将代理的非 JSON 错误显示为中文重试提示', async () => {
  global.fetch=async()=>new Response('Bad Gateway',{status:502,headers:{'Content-Type':'text/plain'}})
  await assert.rejects(()=>parseVideo('https://example.com/video'),/502.*重试/)
})
