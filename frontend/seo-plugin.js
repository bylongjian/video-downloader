import {readFile,writeFile,mkdir} from 'node:fs/promises'
import {resolve} from 'node:path'
import {createServer} from 'vite'
import vue from '@vitejs/plugin-vue'
import {pageForPath,parseSiteUrl,renderHead,renderRobots,renderSitemap} from './src/seo.js'
import {renderLlms} from './src/content/product.js'

function documentHtml(template,key,site,body) {
  let html=template.replace(/<!--seo:start-->[\s\S]*?<!--seo:end-->|<!--page-head-->/,()=>renderHead(key,site))
  html=html.replace(/<!--app:start-->[\s\S]*?<!--app:end-->/,()=>`<!--app:start-->${body}<!--app:end-->`)
  if(key==='notFound'||key==='guide') html=html.replace(/<script\b[^>]*type="module"[^>]*>[\s\S]*?<\/script>/g,'')
  return html
}

export function seoPlugin(siteValue) {
  const site=parseSiteUrl(siteValue)
  let config
  function middleware(server,preview=false) {
    return async(req,res,next)=>{
      if(!['GET','HEAD'].includes(req.method)) return next()
      let path
      try {path=new URL(req.url,'http://localhost').pathname}
      catch {res.statusCode=400;res.setHeader('X-Robots-Tag','noindex');res.end('Bad request');return}
      if(path.startsWith('/api/')) return next()
      try {
        if(path==='/robots.txt' || path==='/sitemap.xml' || path==='/llms.txt') {
          res.setHeader('Content-Type',path.endsWith('.xml')?'application/xml; charset=utf-8':'text/plain; charset=utf-8')
          if(preview) {
            let content
            try {content=await readFile(resolve(config.root,config.build.outDir,path.slice(1)),'utf8')}
            catch(error){if(error.code!=='ENOENT')throw error;res.statusCode=404;res.end();return}
            res.end(req.method==='HEAD'?'':content);return
          }
          if(path==='/sitemap.xml'){res.statusCode=404;res.end();return}
          res.end(req.method==='HEAD'?'':path==='/llms.txt'?renderLlms(''):renderRobots(''));return
        }
        const key=pageForPath(path)
        const internal=path.startsWith('/_pages/')
        const htmlRequest=(req.headers.accept || '').includes('text/html')
        if(key==='notFound' && !internal && !htmlRequest) return next()
        res.statusCode=key==='notFound'?404:200
        res.setHeader('Content-Type','text/html; charset=utf-8')
        let html
        if(preview) {
          const file=key==='home'?'index.html':key==='guide'?'guide/index.html':key==='notFound'?'404.html':`_pages/${key}.html`
          html=await readFile(resolve(config.root,config.build.outDir,file),'utf8')
        } else {
          let body=''
          if(key==='home'||key==='guide'||key==='notFound') body=await (await server.ssrLoadModule('/src/prerender.js')).renderPublicPage(key)
          const template=await server.transformIndexHtml(req.url,await readFile(resolve(config.root,'index.html'),'utf8'))
          html=documentHtml(template,key,'',body)
          if(key==='guide'||key==='notFound') html=html.replace('</head>','<link rel="stylesheet" href="/src/style.css?direct"></head>')
        }
        if(html.includes('name="robots" content="noindex')) res.setHeader('X-Robots-Tag','noindex, follow')
        res.end(req.method==='HEAD'?'':html)
      } catch(error) {next(error)}
    }
  }
  return {
    name:'saveany-public-html',
    configResolved(value){config=value},
    transformIndexHtml(html){return html.replace('<!--page-head-->',()=>renderHead('home',site))},
    configureServer(server){server.middlewares.use(middleware(server))},
    configurePreviewServer(server){server.middlewares.use(middleware(server,true))},
    async closeBundle(){
      if(config.command!=='build') return
      const output=resolve(config.root,config.build.outDir)
      const template=await readFile(resolve(output,'index.html'),'utf8')
      const renderer=await createServer({root:config.root,configFile:false,plugins:[vue()],server:{middlewareMode:true},appType:'custom',logLevel:'error'})
      try {
        const {renderPublicPage}=await renderer.ssrLoadModule('/src/prerender.js')
        await writeFile(resolve(output,'index.html'),documentHtml(template,'home',site,await renderPublicPage('home')))
        await writeFile(resolve(output,'404.html'),documentHtml(template,'notFound',site,await renderPublicPage('notFound')))
        await mkdir(resolve(output,'guide'),{recursive:true})
        await writeFile(resolve(output,'guide/index.html'),documentHtml(template,'guide',site,await renderPublicPage('guide')))
        await mkdir(resolve(output,'_pages'),{recursive:true})
        for(const key of ['workbench','history','queue','billing']) await writeFile(resolve(output,`_pages/${key}.html`),documentHtml(template,key,site,''))
        await writeFile(resolve(output,'robots.txt'),renderRobots(site))
        await writeFile(resolve(output,'llms.txt'),renderLlms(site))
        if(site) await writeFile(resolve(output,'sitemap.xml'),renderSitemap(site))
      } finally {await renderer.close()}
    },
  }
}
