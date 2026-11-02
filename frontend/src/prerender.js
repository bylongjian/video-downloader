import {createSSRApp} from 'vue'
import {renderToString} from 'vue/server-renderer'
import PublicPage from './PublicPage.vue'
import Icon from './components/Icon.vue'

export async function renderPublicPage(page) {
  return renderToString(createSSRApp(PublicPage,{page}).component('Icon',Icon))
}
