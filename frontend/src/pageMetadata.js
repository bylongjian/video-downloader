import {pageForPath,renderHead,parseSiteUrl} from './seo.js'

const site=parseSiteUrl(import.meta.env.DEV ? '' : import.meta.env.VITE_SITE_URL || '')
export function updatePageMetadata() {
  const key=pageForPath(location.pathname)
  const template=document.createElement('template')
  template.innerHTML=renderHead(key,site)
  document.head.querySelectorAll('title,meta[name="description"],meta[name="keywords"],meta[name="robots"],meta[property^="og:"],meta[name^="twitter:"],link[rel="canonical"],#site-structured-data').forEach(node=>node.remove())
  document.head.append(...template.content.children)
}
