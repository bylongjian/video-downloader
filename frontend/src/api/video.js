async function post(path, body, signal) {
  return fetch(`/api/${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal,
  })
}

async function jsonResponse(response) {
  let result
  try { result = await response.json() }
  catch { throw new Error(`服务响应异常（HTTP ${response.status}），请确认后端已启动后重试。`) }
  if (!response.ok || !result.success) {
    throw new Error(result.error || result.detail?.error || result.detail || '请求失败，请稍后重试')
  }
  return result
}

export async function parseVideo(url, signal) {
  return jsonResponse(await post('parse', { url }, signal))
}

export async function getDirectUrl(url, formatId, signal) {
  return jsonResponse(await post('direct-url', { url, format_id: formatId }, signal))
}

export async function downloadViaServer(url, formatId, signal) {
  const response = await post('download', { url, format_id: formatId }, signal)
  if (!response.ok) await jsonResponse(response)
  return response
}

async function saveResponse(response, fallback) {
  if (!response.ok) throw new Error('视频直链暂不可用')
  const type = response.headers.get('content-type') || ''
  if (/text\/html|application\/(json|vnd\.apple\.mpegurl)|mpegurl/i.test(type)) {
    throw new Error('该地址需要服务端处理后下载')
  }
  const disposition = response.headers.get('content-disposition') || ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  const plain = disposition.match(/filename=(?:"((?:\\.|[^"\\])*)"|([^;]+))/i)
  const filename = encoded ? decodeURIComponent(encoded[1]) : plain ? (plain[1]?.replace(/\\(.)/g,'$1') ?? plain[2].trim()) : fallback
  const blob = await response.blob()
  if (!blob.size) throw new Error('下载内容为空，请重新下载')
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename.replace(/[\\/:*?"<>|]/g, '_')
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
}

export async function downloadVideo(url, formatId, onStatus, signal) {
  onStatus('正在获取下载地址…')
  try {
    const { data } = await getDirectUrl(url, formatId, signal)
    if (data.direct_url && !data.requires_proxy) {
      onStatus('正在通过直链下载…')
      const response = await fetch(data.direct_url, { signal, credentials: 'omit' })
      await saveResponse(response, `${data.title || '视频'}.${data.ext || 'mp4'}`)
      return { mode: 'direct', warning: '' }
    }
  } catch (error) {
    if (signal?.aborted || error.name === 'AbortError') throw error
  }
  onStatus('正在由服务器准备文件并下载…')
  const response = await downloadViaServer(url, formatId, signal)
  await saveResponse(response, '视频.mp4')
  const warning = response.headers.get('X-Download-Warning') || ''
  return { mode: 'proxy', warning: decodeURIComponent(warning) }
}
