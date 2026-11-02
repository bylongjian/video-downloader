import { createApp } from 'vue'
import App from './App.vue'
import Icon from './components/Icon.vue'
import './style.css'
import {updatePageMetadata} from './pageMetadata.js'

updatePageMetadata()
createApp(App).component('Icon', Icon).mount('#app')
