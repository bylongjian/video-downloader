import {contentUpdated,faqItems} from './content/product.js'

export const siteName = 'SaveAny（万能视频下载总结器）'
export const pages = {
  home: {path:'/', title:'万能视频下载总结器 - SaveAny | 视频下载与 AI 学习助手', description:'SaveAny 万能视频下载总结器提供视频链接解析、可用格式下载、字幕导出与 AI 摘要、思维导图和视频问答。游客可解析和单条下载；登录后使用 AI、历史和批量任务。平台可用性受视频权限、网络与服务配置影响。', keywords:'万能视频下载总结器,SaveAny,视频下载,AI视频总结,字幕导出,思维导图,视频问答', public:true},
  guide: {path:'/guide/', title:'视频下载与 AI 总结使用指南 - SaveAny | 字幕导出与常见问题', description:'了解 SaveAny 视频下载和 AI 总结步骤、字幕与导图导出方法、免费账号额度及长视频分析范围。先查看平台、字幕和本地转写的使用条件，再开始处理视频。', keywords:'视频下载教程,AI视频总结,字幕导出,视频问答,SaveAny使用指南', public:true},
  workbench: {title:'视频工作台 - SaveAny | 视频下载与 AI 总结', description:'解析视频信息，选择可用格式下载，登录后阅读字幕、生成摘要和思维导图并进行视频问答。', keywords:'视频工作台,视频解析,视频下载,AI总结'},
  history: {title:'我的历史 - SaveAny | 下载与总结记录', description:'查看本人的下载历史与已保存总结，免费恢复已有结果，管理个人记录。', keywords:'下载历史,总结历史,保存结果'},
  queue: {title:'批量任务 - SaveAny | 后台下载与字幕任务', description:'查看本人的后台任务、真实处理进度和失败提示，领取已完成的文件。', keywords:'批量下载,后台任务,字幕任务'},
  billing: {title:'会员与订单 - SaveAny | 套餐权益与订单状态', description:'查看免费账号和会员权益、当前套餐与本人订单。支付配置和会员开通状态以服务端确认为准。', keywords:'会员权益,套餐,订单状态'},
  notFound: {title:'页面未找到 - SaveAny | 404', description:'这个页面不存在，可以返回 SaveAny 首页解析视频或查看功能介绍。', keywords:'SaveAny,页面未找到'},
}

export function pageForPath(path) {
  if(path==='/' || path==='/index.html') return 'home'
  if(path==='/guide' || path==='/guide/' || path==='/guide/index.html') return 'guide'
  if(/^\/history(?:\/(?:summary|download)\/[^/]+)?\/?$/.test(path)) return 'history'
  const match=path.match(/^\/(workbench|queue|billing)\/?$/)
  return match ? match[1] : 'notFound'
}

// 公开站点地址是构建输入；不从访客地址、请求头或视频链接推测。
export function parseSiteUrl(value='') {
  if(!value.trim()) return ''
  const url=new URL(value.trim())
  if(url.protocol!=='https:' || url.username || url.password || url.port || url.pathname!=='/' || url.search || url.hash || !url.hostname.includes('.') || /^(?:\[|\d+\.\d+\.\d+\.\d+$)/.test(url.hostname) || /(?:^|\.)(?:localhost|local)$/.test(url.hostname)) {
    throw new Error('VITE_SITE_URL 必须是正式 HTTPS 根网址，不含端口、路径、查询参数或登录信息。')
  }
  return url.origin
}

export const escapeHtml=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))
export const jsonLd=value=>JSON.stringify(value).replace(/</g,'\\u003c')

export function structuredData(key,site) {
  if(key!=='home' && key!=='guide') return null
  const page=pages[key]
  const about=key==='home' ? {'@type':'SoftwareApplication',name:siteName,applicationCategory:'MultimediaApplication',operatingSystem:'Web',inLanguage:'zh-CN',description:page.description,featureList:['视频链接解析与可用格式下载','字幕 SRT、VTT、TXT 导出','AI 摘要、思维导图与视频问答']} : {'@type':'WebPage',name:page.title,description:page.description,inLanguage:'zh-CN',dateModified:contentUpdated}
  if(site) about.url=site+page.path
  return {'@context':'https://schema.org','@graph':[about,{'@type':'FAQPage',inLanguage:'zh-CN',dateModified:contentUpdated,...(site?{url:site+page.path}:{}),mainEntity:faqItems.map(item=>({'@type':'Question',name:item.question,acceptedAnswer:{'@type':'Answer',text:item.answer}}))}]}
}

export function renderHead(key,site) {
  const page=pages[key],indexable=page.public && site
  const meta=(name,value,attr='name')=>`<meta ${attr}="${name}" content="${escapeHtml(value)}">`
  const tags=[`<title>${escapeHtml(page.title)}</title>`,meta('description',page.description),meta('keywords',page.keywords),meta('robots',indexable?'index, follow':'noindex, follow'),meta('og:title',page.title,'property'),meta('og:description',page.description,'property'),meta('og:type','website','property'),meta('og:locale','zh_CN','property'),meta('og:site_name',siteName,'property'),meta('twitter:card',indexable?'summary_large_image':'summary'),meta('twitter:title',page.title),meta('twitter:description',page.description)]
  if(indexable) tags.push(`<link rel="canonical" href="${escapeHtml(site+page.path)}">`,meta('og:url',site+page.path,'property'),meta('og:image',site+'/images/saveany-share.png','property'),meta('og:image:width','1200','property'),meta('og:image:height','630','property'),meta('og:image:alt',siteName+'：视频下载与 AI 学习助手','property'),meta('twitter:image',site+'/images/saveany-share.png'),meta('twitter:image:alt',siteName+'：视频下载与 AI 学习助手'))
  const data=structuredData(key,site)
  if(data) tags.push(`<script type="application/ld+json" id="site-structured-data">${jsonLd(data)}</script>`)
  return '<!--seo:start-->\n'+tags.join('\n')+'\n<!--seo:end-->'
}

export function renderRobots(site) {
  return site ? `User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /_pages/\n\nSitemap: ${site}/sitemap.xml\n` : 'User-agent: *\nDisallow: /\n'
}

export function renderSitemap(site) {
  return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+Object.values(pages).filter(page=>page.public).map(page=>`<url><loc>${escapeHtml(site+page.path)}</loc></url>`).join('')+'</urlset>\n'
}
