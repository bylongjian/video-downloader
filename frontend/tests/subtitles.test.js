import {test} from 'node:test'
import assert from 'node:assert/strict'

const storage=new Map()
global.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}
global.window=new EventTarget()
const auth=await import('../src/api/auth.js')
const api=await import('../src/api/subtitles.js').catch(()=>({}))

test('字幕缓存仅GET读取，显式生成保留来源、force和独立request_key',async()=>{
  assert.equal(typeof api.translateSubtitles,'function')
  auth.setToken('subtitle-session')
  const calls=[],controller=new AbortController()
  global.fetch=async(path,options)=>{calls.push([path,options]);return Response.json({success:true,data:null})}
  await api.fetchTranslation('summary','source+/=','ja',controller.signal)
  await api.fetchTranscription('https://example.com/watch?v=one&list=two',controller.signal)
  const translation={request_key:'translate-one',source_kind:'summary',source_id:'saved-id',target_language:'ja',force:true}
  const transcription={request_key:'asr-one',url:'https://example.com/video',language:'auto',force:false}
  await api.translateSubtitles(translation,controller.signal)
  await api.createTranscription(transcription,controller.signal)
  assert.equal(new URL(calls[0][0],'http://localhost').searchParams.get('source_id'),'source+/=')
  assert.equal(new URL(calls[1][0],'http://localhost').searchParams.get('url'),'https://example.com/watch?v=one&list=two')
  assert.deepEqual(calls.map(([path,options])=>[path.split('?')[0],options.method]),[
    ['/api/subtitles/translation','GET'],['/api/transcriptions/result','GET'],['/api/subtitles/translate','POST'],['/api/transcriptions','POST'],
  ])
  assert.deepEqual(JSON.parse(calls[2][1].body),translation)
  assert.deepEqual(JSON.parse(calls[3][1].body),transcription)
  for(const [,options] of calls){assert.equal(options.headers.Authorization,'Bearer subtitle-session');assert.equal(options.signal,controller.signal)}
  await api.deleteTranslation('translation/id');await api.deleteTranscription('transcription/id')
  assert.deepEqual(calls.slice(4).map(([path,options])=>[path,options.method]),[['/api/subtitles/translations/translation%2Fid','DELETE'],['/api/transcriptions/transcription%2Fid','DELETE']])
})

test('未配置与额度错误保留服务端提示；旧会话401不能清除新账号',async()=>{
  assert.equal(typeof api.createTranscription,'function')
  auth.setToken('old-account')
  global.fetch=async()=>Response.json({success:false,error:'请先准备本地 ASR 模型',code:'ASR_NOT_READY'},{status:503})
  await assert.rejects(()=>api.createTranscription({}),cause=>cause.status===503&&cause.code==='ASR_NOT_READY'&&cause.message.includes('准备'))
  const quota={translation:{remaining:0}}
  global.fetch=async()=>Response.json({success:false,error:'翻译次数已用完',code:'QUOTA_EXHAUSTED',quota},{status:429})
  await assert.rejects(()=>api.translateSubtitles({}),cause=>cause.status===429&&cause.quota.translation.remaining===0)
  let finish
  global.fetch=()=>new Promise(resolve=>finish=resolve)
  const pending=api.fetchTranscription('https://example.com/video')
  auth.setToken('new-account')
  finish(Response.json({success:false,error:'请登录',code:'SESSION_INVALID'},{status:401}))
  await assert.rejects(()=>pending,cause=>cause.status===401)
  assert.equal(auth.getToken(),'new-account')
})

test('网络、非JSON和取消都保留失败状态',async()=>{
  assert.equal(typeof api.fetchTranslation,'function')
  global.fetch=async()=>{throw new TypeError('fetch failed')}
  await assert.rejects(()=>api.fetchTranslation('summary','one','en'),/网络|连接/)
  global.fetch=async()=>new Response('<html>offline</html>',{status:503})
  await assert.rejects(()=>api.fetchTranscription('one'),/503/)
  const controller=new AbortController();controller.abort()
  global.fetch=async()=>{throw new DOMException('Aborted','AbortError')}
  await assert.rejects(()=>api.fetchTranscription('one',controller.signal),cause=>cause.name==='AbortError')
})
